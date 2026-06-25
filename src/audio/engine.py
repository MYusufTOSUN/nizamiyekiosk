"""BilimFest ses çekirdeği — kendini kalibre eden, sağlam full-duplex motor.

Tasarım hedefleri (sıfırdan yeniden kuruldu):
1. **Doğru cihaz kullanımı**: mikrofonu NATIVE örnekleme hızında yakala (ör. Realtek
   44.1 kHz), Whisper için 16 kHz'e KENDİMİZ anti-aliasing'li indir (MME'nin kör
   içsel resample'ına güvenme -> seviye/kalite sorunlarını eler).
2. **Kendini kalibrasyon**: açılışta BOŞTAYKEN ortam gürültü tabanını ölç, tüm
   eşikleri (dinleme, gürültü-kapısı, barge) ölçülen tabana GÖRE türet. Kısa bir
   test sesi çalıp mikrofona sızan eko'yu (coupling) + oda yankısını (reverb)
   ölçer; barge'ın referans-kapısı parametrelerini bundan türetir.
3. **Sağlam barge-in (referans-kapılı)**: barge eşiğini mikrofonun KENDİ sinyalinden
   türetmez (döngüsel + kendini keser). El-Cezerî'nin gerçek TTS çıkışını referans
   alır: o ses çıkarırken detektör KAPALI, yalnız o sustuğunda/duraksadığında
   mikrofon hâlâ yüksekse = ziyaretçi. Kendi sesi barge'ı ASLA tetikleyemez.

Bu modül donanımdan bağımsız test edilebilir saf yardımcılar + ince I/O sarmalayıcılar
içerir; festival kiosk'u ve scripts/audio_check.py teşhis aracı bunu kullanır.
"""
from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sounddevice as sd

from src.core.logger import get_logger

_log = get_logger(component="audio.engine")

TARGET_SR = 16000          # Whisper hedef hızı
FRAME_MS = 30              # kare süresi (VAD/barge çözünürlüğü)
SPEAKER_SR = 24000         # XTTS çıkış hızı


def _diag(msg: str) -> None:
    """Festival operatör KONSOL teşhisi (configure_logging WARNING olduğundan
    _log.info görünmez; sahada RMS/eşik görmek için doğrudan print)."""
    print(msg)  # noqa: T201


# ============================ saf yardımcılar ================================
def rms(x: np.ndarray) -> float:
    """Kare/dizi RMS'i (örnekleme hızından bağımsız)."""
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if x.size else 0.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float32), p))


def resample_to_16k(audio_native: np.ndarray, native_sr: int) -> np.ndarray:
    """Native-hız mono float32 -> 16 kHz, anti-aliasing'li (scipy resample_poly).

    44100->16000 gibi tam-bölünmeyen oranlarda bile polyphase filtre aliasing'i
    keser (Whisper doğruluğu için kritik). Hız zaten 16k ise dokunmaz."""
    if native_sr == TARGET_SR or audio_native.size == 0:
        return audio_native.astype(np.float32)
    from math import gcd

    g = gcd(TARGET_SR, native_sr)
    up, down = TARGET_SR // g, native_sr // g
    try:
        from scipy.signal import resample_poly

        return resample_poly(audio_native, up, down).astype(np.float32)
    except Exception:  # noqa: BLE001 — scipy yoksa/hatasında doğrusal yedek
        n = int(round(audio_native.size * TARGET_SR / native_sr))
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        idx = np.linspace(0, audio_native.size - 1, n)
        return np.interp(idx, np.arange(audio_native.size), audio_native).astype(np.float32)


def normalize_for_stt(audio: np.ndarray, target_peak: float = 0.5,
                      max_gain: float = 12.0) -> np.ndarray:
    """Decoder beslemesi için seviye normalize (kazanç TAVANLI -> gürültü şişmez)."""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-6:
        return audio
    gain = min(target_peak / peak, max_gain)
    return (audio * gain).astype(np.float32)


# ============================ kalibrasyon ===================================
@dataclass
class Calibration:
    """Ölçülen ortam/eko'dan türetilmiş tüm eşikler (native-hız RMS biriminde)."""
    native_sr: int
    ambient_rms: float          # ortam taban (medyan)
    ambient_p95: float          # ortam 95. persentil (gürültü tepe bandı)
    listen_threshold: float     # konuşma onset eşiği (VAD)
    min_voice_rms: float        # bunun altı = gürültü, klip at
    barge_threshold: float      # El-Cezerî sessizken ziyaretçi bunu aşmalı
    echo_coupling: float | None # hoparlör->mik sızıntı oranı (echo/out), ölçülmediyse None
    barge_ref_quiet: float      # TTS çıkış RMS < bu => barge armed
    echo_guard_ms: int          # reverb decay + hangover
    snr_ok: bool                # ortam-konuşma ayrımı yapılabilir mi
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        cup = f"{self.echo_coupling:.2f}" if self.echo_coupling is not None else "—"
        return (
            f"native_sr={self.native_sr} ortam_rms={self.ambient_rms:.4f} "
            f"ortam_p95={self.ambient_p95:.4f}\n"
            f"  dinleme_eşik={self.listen_threshold:.4f} gürültü_kapısı={self.min_voice_rms:.4f} "
            f"barge_eşik={self.barge_threshold:.4f}\n"
            f"  eko_coupling={cup} barge_ref_quiet={self.barge_ref_quiet:.4f} "
            f"echo_guard={self.echo_guard_ms}ms snr_ok={self.snr_ok}"
        )


def derive_from_ambient(ambient_rms: float, ambient_p95: float,
                        overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Ölçülen ortam tabanından eşikleri TÜRET (deterministik, test edilebilir).

    Mantık: konuşma ortam gürültüsünün belirgin üstünde olmalı. Eşikler ortam
    p95'ine ORANLA kurulur + mutlak alt sınırlarla kelepçelenir. overrides ile
    CLI/operatör tek tek değeri ezebilir."""
    overrides = overrides or {}
    # Mutlak tabanlar DÜŞÜK tutuldu: çok sessiz/kısık mikte (ölçülen p95~0.002)
    # eski 0.010 tabanı konuşma seviyesine ÇOK yakın olup onset'i kaçırıyordu.
    listen = max(ambient_p95 * 3.0, 0.005)
    min_voice = max(ambient_p95 * 1.8, 0.003)
    # barge dinlemeden biraz daha katı (playback eko-kalıntısı + ortam üstünde olsun)
    barge = max(ambient_p95 * 4.0, listen * 1.3, 0.015)
    out = {
        "listen_threshold": listen,
        "min_voice_rms": min_voice,
        "barge_threshold": barge,
    }
    out.update({k: v for k, v in overrides.items() if k in out})
    return out


def default_calibration(native_sr: int, overrides: dict[str, float] | None = None) -> Calibration:
    """Kalibrasyon atlandığında (--no-calibrate) statik-varsayılan eşikler.

    Makul bir ortam tabanı (p95~0.012) varsayar; overrides ile ezilebilir.
    Ölçüm yapmaz — sahada calibrate() yeğlenir."""
    overrides = overrides or {}
    th = derive_from_ambient(0.008, 0.012, overrides)
    return Calibration(
        native_sr=native_sr, ambient_rms=0.008, ambient_p95=0.012,
        listen_threshold=th["listen_threshold"], min_voice_rms=th["min_voice_rms"],
        barge_threshold=th["barge_threshold"], echo_coupling=None,
        barge_ref_quiet=float(overrides.get("barge_ref_quiet", 0.02)),
        echo_guard_ms=int(overrides.get("echo_guard_ms", 350)), snr_ok=True,
        notes=["kalibrasyon atlandı — statik varsayılanlar (sahada calibrate yeğle)"],
    )


def _collect_rms(read_frame: Callable[[float], np.ndarray | None], seconds: float) -> list[float]:
    vals: list[float] = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        f = read_frame(0.1)
        if f is not None and f.size:
            vals.append(rms(f))
    return vals


def calibrate(
    mic: MicStream,
    *,
    ambient_seconds: float = 3.0,
    speaker: SpeakerStream | None = None,
    test_pcm: np.ndarray | None = None,
    overrides: dict[str, float] | None = None,
) -> Calibration:
    """Açılış kalibrasyonu: ortam tabanı + (varsa) eko/reverb ölç, eşikleri türet.

    - ambient: ``ambient_seconds`` boyunca hoparlör SESSİZ iken mikrofonu ölç.
    - eko: ``speaker`` ve ``test_pcm`` verilirse test sesini çalıp mikrofona sızan
      eko + reverb decay'ini ölç -> barge_ref_quiet + echo_guard türet.
    """
    overrides = overrides or {}
    notes: list[str] = []

    mic.drain()
    amb = _collect_rms(mic.read_frame, ambient_seconds)
    if not amb:
        notes.append("UYARI: ortam ölçümünde kare gelmedi — mikrofon ölü olabilir.")
        amb = [0.01]
    ambient_rms = float(np.median(amb))
    ambient_p95 = percentile(amb, 95)
    ambient_max = max(amb)

    th = derive_from_ambient(ambient_rms, ambient_p95, overrides)

    # SNR makullüğü: ortam tepe bandı çok yüksekse konuşma ayrımı zor olur.
    snr_ok = ambient_p95 < 0.05
    if not snr_ok:
        notes.append(
            f"UYARI: ortam gürültüsü yüksek (p95={ambient_p95:.3f}). Mik gain'ini "
            "düşür ya da daha sessiz/yakın mik kullan; barge yanlış-tetik riski artar."
        )
    if ambient_max > 0 and ambient_p95 / max(ambient_rms, 1e-6) > 6:
        notes.append("Not: ortamda ara ara yüksek gürültü patlamaları var (darbeli).")

    echo_coupling: float | None = None
    ref_quiet = float(overrides.get("barge_ref_quiet", 0.02))
    echo_guard = int(overrides.get("echo_guard_ms", 350))

    if speaker is not None and test_pcm is not None and test_pcm.size:
        echo_coupling, reverb_ms, echo_mic = _measure_echo(mic, speaker, test_pcm, ambient_rms)
        notes.append(f"Eko ölçümü: mik_eko={echo_mic:.4f} coupling={echo_coupling:.2f} "
                     f"reverb~{reverb_ms:.0f}ms")
        if "echo_guard_ms" not in overrides:
            echo_guard = int(min(max(reverb_ms + 120, 200), 800))
        if "barge_ref_quiet" not in overrides and echo_coupling > 1e-3:
            # armed iken (out<ref_quiet) eko kalıntısı barge eşiğinin ALTINDA kalsın:
            #   ref_quiet * coupling < barge_threshold  -> ref_quiet < barge/coupling
            ref_quiet = float(min(max((th["barge_threshold"] / echo_coupling) * 0.6, 0.005), 0.10))

    return Calibration(
        native_sr=mic.native_sr,
        ambient_rms=ambient_rms,
        ambient_p95=ambient_p95,
        listen_threshold=th["listen_threshold"],
        min_voice_rms=th["min_voice_rms"],
        barge_threshold=th["barge_threshold"],
        echo_coupling=echo_coupling,
        barge_ref_quiet=ref_quiet,
        echo_guard_ms=echo_guard,
        snr_ok=snr_ok,
        notes=notes,
    )


def _measure_echo(
    mic: MicStream, speaker: SpeakerStream, test_pcm: np.ndarray, ambient_rms: float,
) -> tuple[float, float, float]:
    """Test sesini çal + mikrofonu ölç -> (coupling, reverb_ms, echo_mic_rms)."""
    out_rms = rms(test_pcm.astype(np.float32) / 32768.0)
    play_dur = test_pcm.size / float(speaker.sr)
    mic.drain()
    speaker.play_array(test_pcm)  # NON-BLOCKING (sd.play); arka planda çalar
    during: list[float] = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < play_dur:
        f = mic.read_frame(0.05)
        if f is not None and f.size:
            during.append(rms(f))
    # reverb: hoparlör durduktan sonra mik ortam seviyesine dönene kadar geçen süre
    decay_t0 = time.monotonic()
    reverb_ms = 0.0
    while time.monotonic() - decay_t0 < 1.0:
        f = mic.read_frame(0.05)
        if f is not None and rms(f) < ambient_rms * 1.6:
            reverb_ms = (time.monotonic() - decay_t0) * 1000.0
            break
    else:
        reverb_ms = 350.0
    echo_mic = float(np.median(during)) if during else ambient_rms
    echo_only = max(1e-6, echo_mic - ambient_rms)
    coupling = echo_only / max(1e-6, out_rms)
    return coupling, reverb_ms, echo_mic


# ============================ mikrofon akışı ================================
class MicStream:
    """Native-hız yakalama + ring-buffer + sağlık takibi. Kanalları mono'ya indirir."""

    def __init__(self, device: Any, native_sr: int | None = None, queue_max: int = 256) -> None:
        self.device = device
        info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
        self.native_sr = int(native_sr or info["default_samplerate"])
        self.blocksize = max(1, int(self.native_sr * FRAME_MS / 1000.0))
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_max)
        self._stream: Any = None
        self.last_frame_ts = time.monotonic()
        self.status_errors = 0

    def _cb(self, indata: Any, _f: int, _t: Any, status: Any) -> None:
        if status:
            self.status_errors += 1
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        frame = np.asarray(mono, dtype=np.float32).copy()
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.q.get_nowait()
            with contextlib.suppress(queue.Full):
                self.q.put_nowait(frame)
        self.last_frame_ts = time.monotonic()

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.native_sr, channels=1, dtype="float32",
            blocksize=self.blocksize, device=self.device, callback=self._cb,
        )
        self._stream.start()
        self.last_frame_ts = time.monotonic()

    def restart(self) -> None:
        self.stop()
        self.start()

    def read_frame(self, timeout: float = 0.1) -> np.ndarray | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        with contextlib.suppress(queue.Empty):
            while True:
                self.q.get_nowait()

    def stale_for(self) -> float:
        return time.monotonic() - self.last_frame_ts

    def stop(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None


# ============================ hoparlör çıkışı ===============================
class SpeakerStream:
    """``sd.play`` tabanlı çıkış + çıkış-RMS zarfı (referans-kapılı barge için).

    ÖNEMLİ: sd.OutputStream(...).start() + bloklayan write() bazı Windows MME
    kurulumlarında AÇILIRKEN KİLİTLENİYOR (festival PC'sinde gözlendi: kalibrasyon
    Enter sonrası donuyordu). ``sd.play`` (eski, kanıtlı-çalışan yol) kullanıyoruz:
    stream'i sounddevice yönetir, biz tüm tamponu veririz, çıkış zarfını da
    buffer-pozisyonundan ZAMANLA hesaplarız (referans-kapısı için stream'in iç
    durumuna ihtiyaç yok). Tek-anda-tek-çalma (kiosk turu/teşhis) varsayımı geçerli;
    global sd.play/sd.stop bu kullanım için güvenli (eski kod da böyleydi)."""

    def __init__(self, sr: int = SPEAKER_SR, device: Any = None,
                 latency: float = 0.12) -> None:
        self.sr = sr
        self.device = device
        self._lat = latency
        self._stopped = False
        self._t0: float | None = None
        self._frame_s = FRAME_MS / 1000.0
        self._env: np.ndarray = np.zeros(0, dtype=np.float32)  # per-frame çıkış RMS
        self._playing = False

    @property
    def latency(self) -> float:
        return self._lat

    def _build_env(self, arr_int16: np.ndarray) -> np.ndarray:
        step = max(1, int(self.sr * self._frame_s))
        x = arr_int16.astype(np.float32) / 32768.0
        n = (x.size + step - 1) // step
        env = np.zeros(n, dtype=np.float32)
        for i in range(n):
            seg = x[i * step : (i + 1) * step]
            env[i] = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        return env

    def play_array(self, arr_int16: np.ndarray) -> None:
        """Tüm diziyi sd.play ile çal (NON-BLOCKING; sd.wait ile beklenir).
        Çıkış zarfını önceden hesaplar (echo_active referans-kapısı için)."""
        if self._stopped or arr_int16.size == 0:
            return
        # C-contiguous + writable kopya: read-only frombuffer view'i sd.play'e
        # güvenle ver (base-buffer/dtype sürprizleri olmasın).
        arr = np.ascontiguousarray(arr_int16, dtype=np.int16)
        self._env = self._build_env(arr)
        self._t0 = time.monotonic()
        self._playing = True
        try:
            if self.device is not None:
                sd.play(arr, self.sr, device=self.device)
            else:
                sd.play(arr, self.sr)
        except Exception as exc:  # noqa: BLE001 — sessizce yutma; cihaz sorununu GÖR
            self._playing = False
            _diag(f"[HOPARLÖR HATA] sd.play açılamadı: {exc}")

    def echo_active(self, window_s: float) -> float:
        """Şu an (latency+reverb ile) mikrofonda duyulan TTS çıkışının yaklaşık
        MAX RMS'i (0..1). El-Cezerî sustuktan ``window_s`` sonra 0'a düşer."""
        if self._t0 is None or not self._playing or self._env.size == 0:
            return 0.0
        elapsed = time.monotonic() - self._t0
        audio_dur = self._env.size * self._frame_s
        if elapsed > audio_dur + window_s:   # çalma bitti + reverb dindi
            return 0.0
        hi = min(self._env.size, int(elapsed / self._frame_s) + 1)
        lo = max(0, int((elapsed - window_s) / self._frame_s))
        if hi <= lo or lo >= self._env.size:
            return float(self._env[-1])      # yalnız kuyruk reverb penceresinde
        return float(self._env[lo:hi].max())

    def wait_done(self) -> None:
        """Çalma bitene kadar bekle (barge yoksa). Bloklar ama sd.wait güvenli."""
        if self._stopped:
            return
        with contextlib.suppress(Exception):
            sd.wait()
        self._playing = False

    def stop(self) -> None:
        self._stopped = True
        self._playing = False
        with contextlib.suppress(Exception):
            sd.stop()

    def close(self) -> None:
        self.stop()


# ============================ endpointing ===================================
@dataclass
class EndpointConfig:
    onset_ms: int = 120             # konuşma başı (4 kare) — kısık mikte daha çevik
    silence_ms: int = 1200          # söyleyiş sonrası bekleme (2000->1200: 'geç' hissini azalt)
    silence_ms_short: int = 1800    # kısa söyleyiş -> biraz daha sabırlı (çocuk molası)
    short_utt_ms: int = 1500
    max_listen_ms: int = 15000


class Endpointer:
    """Onset + iki-aşamalı sessizlik endpointing. Native kare RMS'i ile çalışır,
    biten söyleyişi 16 kHz float32 olarak döndürür (Whisper'a hazır)."""

    def __init__(self, cal: Calibration, cfg: EndpointConfig | None = None) -> None:
        self.cal = cal
        self.cfg = cfg or EndpointConfig()

    def listen(
        self, mic: MicStream, is_active: Callable[[], bool],
    ) -> tuple[np.ndarray | None, str]:
        cfg, cal = self.cfg, self.cal
        mic.drain()  # bayat/eski kareleri at — taze dinle (gecikmeyi önler)
        fr_ms = FRAME_MS
        onset_need = max(1, int(cfg.onset_ms / fr_ms))
        frames: list[np.ndarray] = []
        started = False
        speech_run = silence_run = 0
        peak_rms = 0.0
        # WALL-CLOCK deadline: kare-sayısı DEĞİL (mikrofon stall edip None döndürürse
        # kare-sayısı artmaz, eski kod sonsuz dönerdi). Süre dolunca her hâlde biter.
        deadline = time.monotonic() + cfg.max_listen_ms / 1000.0
        hit_deadline = False
        while True:
            if not is_active():
                return None, "iptal"
            if time.monotonic() >= deadline:
                hit_deadline = True
                break
            f = mic.read_frame(0.2)
            if f is None:
                continue
            r = rms(f)
            peak_rms = max(peak_rms, r)
            loud = r > cal.listen_threshold
            if not started:
                if loud:
                    speech_run += 1
                    if speech_run >= onset_need:
                        started = True
                        frames.append(f)
                else:
                    # tek dip onset'i ÖLDÜRMESİN: sıfırla değil, azalt (yumuşak başlangıç)
                    speech_run = max(0, speech_run - 1)
                continue
            frames.append(f)
            if loud:
                silence_run = 0
            else:
                silence_run += 1
                spoken_ms = len(frames) * fr_ms
                need_ms = cfg.silence_ms_short if spoken_ms < cfg.short_utt_ms else cfg.silence_ms
                if silence_run >= max(1, int(need_ms / fr_ms)):
                    break
        if not started or not frames:
            # TEŞHİS (konsola): konuşma RMS'i dinleme eşiğini aştı mı? Sahada kalibrasyon.
            _diag(f"[vad] onset YOK  peak={peak_rms:.4f}  esik={cal.listen_threshold:.4f}"
                  f"  (konusma esigi asmadi -> mik kisik / esik yuksek)")
            return None, "boş"
        native = np.concatenate(frames)
        utt_rms = rms(native)
        reason = "kesildi" if hit_deadline else "ok"
        if utt_rms < cal.min_voice_rms:
            reason = "çok_kısık"
        _diag(f"[vad] frames={len(frames)} utt_rms={utt_rms:.4f} peak={peak_rms:.4f} "
              f"esik={cal.listen_threshold:.4f} kapi={cal.min_voice_rms:.4f} -> {reason}")
        if reason == "çok_kısık":
            return None, reason
        audio16 = resample_to_16k(native, cal.native_sr)
        audio16 = normalize_for_stt(audio16)
        return audio16, reason


# ============================ referans-kapılı barge =========================
@dataclass
class BargeConfig:
    barge_ms: int = 450             # gereken sürekli ziyaretçi konuşması
    cooldown_ms: int = 250          # barge/playback sonrası drain+guard


class BargeDetector:
    """REFERANS-KAPILI barge: El-Cezerî'nin TTS çıkışını referans alır.

    El-Cezerî ses çıkarırken (echo_active > ref_quiet) detektör KAPALI. Yalnız o
    sustuğunda/duraksadığında mikrofon hâlâ barge_threshold üstündeyse = ziyaretçi.
    Kendi sesi (hece dinamiği ne olursa olsun) barge'ı ASLA tetikleyemez."""

    def __init__(self, cal: Calibration, cfg: BargeConfig | None = None) -> None:
        self.cal = cal
        self.cfg = cfg or BargeConfig()

    def monitor(self, mic: MicStream, speaker: SpeakerStream,
                stop_evt: threading.Event, barge_evt: threading.Event) -> None:
        cal, cfg = self.cal, self.cfg
        need = max(1, int(cfg.barge_ms / FRAME_MS))
        window = speaker.latency + cal.echo_guard_ms / 1000.0
        speech = 0
        while not stop_evt.is_set():
            f = mic.read_frame(0.1)
            if f is None:
                continue
            if speaker.echo_active(window) > cal.barge_ref_quiet:
                speech = 0          # El-Cezerî konuşuyor -> barge KAPALI
                continue
            if rms(f) > cal.barge_threshold:
                speech += 1
                if speech >= need:
                    speaker.stop()
                    barge_evt.set()
                    return
            else:
                speech = 0


# ============================ test tonu (teşhis) ============================
def make_test_tone(seconds: float = 1.6, sr: int = SPEAKER_SR, level: float = 0.3) -> np.ndarray:
    """Kalibrasyon/teşhis için konuşma-bandı çok-tonlu burst (int16 @ sr)."""
    t = np.arange(int(seconds * sr)) / sr
    wave = (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 700 * t)
            + 0.5 * np.sin(2 * np.pi * 1400 * t))
    wave = wave / np.max(np.abs(wave)) * level
    # baş/son rampası (klik olmasın)
    ramp = int(0.02 * sr)
    if ramp > 0 and wave.size > 2 * ramp:
        wave[:ramp] *= np.linspace(0, 1, ramp)
        wave[-ramp:] *= np.linspace(1, 0, ramp)
    return (wave * 32767).astype(np.int16)


__all__ = [
    "TARGET_SR", "FRAME_MS", "SPEAKER_SR",
    "rms", "percentile", "resample_to_16k", "normalize_for_stt",
    "Calibration", "derive_from_ambient", "calibrate", "default_calibration",
    "MicStream", "SpeakerStream",
    "EndpointConfig", "Endpointer", "BargeConfig", "BargeDetector",
    "make_test_tone",
]
