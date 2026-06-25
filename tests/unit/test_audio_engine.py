"""Ses motoru saf-mantık testleri (donanımsız): kalibrasyon türetme, resample,
endpointing, referans-kapılı barge."""

from __future__ import annotations

import collections
import threading
import time

import numpy as np

from src.audio import engine as E


def frame(value: float, n: int = 480) -> np.ndarray:
    return np.full(n, float(value), dtype=np.float32)


class FakeMic:
    def __init__(self, frames: list[np.ndarray], native_sr: int = 16000) -> None:
        self.native_sr = native_sr
        self._frames = collections.deque(frames)

    def drain(self) -> None:
        # No-op: scripted kareler 'drain sonrası taze akış'ı temsil eder
        # (Endpointer.listen başta mic.drain() çağırır; gerçek kuyruğu boşaltır).
        return

    def read_frame(self, timeout: float = 0.1) -> np.ndarray | None:
        return self._frames.popleft() if self._frames else None


# ---------------- resample ----------------
def test_resample_44100_to_16000_length() -> None:
    x = np.sin(np.linspace(0, 50, 44100)).astype(np.float32)  # 1 sn @ 44.1k
    y = E.resample_to_16k(x, 44100)
    assert abs(y.size - 16000) <= 2  # ~16000 örnek
    assert y.dtype == np.float32


def test_resample_noop_when_already_16k() -> None:
    x = np.ones(1600, dtype=np.float32)
    y = E.resample_to_16k(x, 16000)
    assert y.size == 1600


# ---------------- normalize ----------------
def test_normalize_caps_gain() -> None:
    quiet = (np.ones(100) * 0.001).astype(np.float32)  # çok kısık
    out = E.normalize_for_stt(quiet, target_peak=0.5, max_gain=12.0)
    assert np.max(np.abs(out)) <= 0.5 * 1.01
    # kazanç tavanı: 0.001*12 = 0.012, 0.5'e ZORLANMAZ (gürültü şişmez)
    assert np.max(np.abs(out)) <= 0.013


# ---------------- kalibrasyon türetme ----------------
def test_derive_scales_with_ambient() -> None:
    low = E.derive_from_ambient(0.005, 0.008)
    high = E.derive_from_ambient(0.03, 0.05)
    assert high["listen_threshold"] > low["listen_threshold"]
    assert high["barge_threshold"] > low["barge_threshold"]
    # barge daima dinlemeden katı
    assert high["barge_threshold"] >= high["listen_threshold"]


def test_derive_floor_when_silent() -> None:
    d = E.derive_from_ambient(0.0, 0.0)  # ölü-sessiz ortam → mutlak tabanlar
    assert d["listen_threshold"] >= 0.005
    assert d["min_voice_rms"] >= 0.003
    assert d["barge_threshold"] >= 0.015


def test_derive_overrides() -> None:
    d = E.derive_from_ambient(0.01, 0.02, overrides={"barge_threshold": 0.2})
    assert d["barge_threshold"] == 0.2


def test_calibrate_ambient_only() -> None:
    # 100 ortam karesi (~3s'lik), düşük gürültü
    frames = [frame(0.01) for _ in range(100)]
    mic = FakeMic(frames)
    cal = E.calibrate(mic, ambient_seconds=0.0)  # 0s → tek geçişte topla
    # ambient_seconds=0 → döngü hiç dönmez; en az fallback değer
    assert cal.listen_threshold > 0
    assert cal.barge_threshold >= cal.listen_threshold


# ---------------- endpointer ----------------
def _cal(native_sr: int = 16000) -> E.Calibration:
    return E.Calibration(
        native_sr=native_sr, ambient_rms=0.005, ambient_p95=0.01,
        listen_threshold=0.10, min_voice_rms=0.02, barge_threshold=0.15,
        echo_coupling=0.5, barge_ref_quiet=0.02, echo_guard_ms=300, snr_ok=True,
    )


def test_endpointer_detects_utterance() -> None:
    cfg = E.EndpointConfig(onset_ms=60, silence_ms=150, silence_ms_short=150,
                           short_utt_ms=0, max_listen_ms=5000)
    ep = E.Endpointer(_cal(), cfg)
    # 4 yüksek (onset+konuşma) + 6 sessiz (≥5 → endpoint)
    frames = [frame(0.3)] * 4 + [frame(0.0)] * 6
    audio, reason = ep.listen(FakeMic(frames), lambda: True)
    assert audio is not None and reason == "ok"
    assert audio.dtype == np.float32 and audio.size > 0


def test_endpointer_rejects_pure_noise() -> None:
    cfg = E.EndpointConfig(onset_ms=60, max_listen_ms=300)  # küçük deadline → hızlı biter
    ep = E.Endpointer(_cal(), cfg)
    frames = [frame(0.02)] * 40  # hep eşik-altı → onset hiç olmaz; sonra deadline
    audio, reason = ep.listen(FakeMic(frames), lambda: True)
    assert audio is None and reason == "boş"


def test_endpointer_gates_too_quiet_utterance() -> None:
    # listen_threshold'u aşan ama min_voice'un altında kalan zayıf söyleyiş
    cal = E.Calibration(
        native_sr=16000, ambient_rms=0.005, ambient_p95=0.01,
        listen_threshold=0.05, min_voice_rms=0.30, barge_threshold=0.15,
        echo_coupling=None, barge_ref_quiet=0.02, echo_guard_ms=300, snr_ok=True,
    )
    cfg = E.EndpointConfig(onset_ms=60, silence_ms=150, silence_ms_short=150,
                           short_utt_ms=0, max_listen_ms=5000)
    ep = E.Endpointer(cal, cfg)
    frames = [frame(0.08)] * 4 + [frame(0.0)] * 6  # 0.08>listen ama tüm-RMS<0.30
    audio, reason = ep.listen(FakeMic(frames), lambda: True)
    assert audio is None and reason == "çok_kısık"


# ---------------- referans-kapılı barge ----------------
class StubSpeaker:
    def __init__(self, echo_val: float) -> None:
        self.echo_val = echo_val
        self.latency = 0.15
        self.stopped = False

    def echo_active(self, w: float) -> float:
        return self.echo_val

    def stop(self) -> None:
        self.stopped = True


def _run_barge(echo_val: float, mic_rms_seq: list[float], cal: E.Calibration) -> bool:
    mic = FakeMic([frame(r) for r in mic_rms_seq])
    spk = StubSpeaker(echo_val)
    stop_evt, barge_evt = threading.Event(), threading.Event()
    det = E.BargeDetector(cal, E.BargeConfig(barge_ms=450))
    t = threading.Thread(target=det.monitor, args=(mic, spk, stop_evt, barge_evt))
    t.start()
    for _ in range(60):
        if barge_evt.is_set() or not mic._frames:
            break
        time.sleep(0.01)
    time.sleep(0.1)
    stop_evt.set()
    t.join(timeout=1.0)
    return barge_evt.is_set()


def test_barge_ignores_el_cezeri_echo() -> None:
    cal = _cal()  # barge_threshold 0.15, ref_quiet 0.02
    # El-Cezerî YÜKSEK (echo 0.1>ref_quiet) + mik eko 0.4 → barge OLMAMALI
    assert _run_barge(0.1, [0.4] * 40, cal) is False


def test_barge_fires_on_visitor_during_silence() -> None:
    cal = _cal()
    # El-Cezerî sessiz (echo 0.0) + ziyaretçi sürekli 0.4 > 0.15 → barge OLMALI
    assert _run_barge(0.0, [0.4] * 25, cal) is True


def test_barge_ignores_intermittent_noise() -> None:
    cal = _cal()
    seq = ([0.4] * 5 + [0.0] * 1) * 8  # kesik → sürekli değil
    assert _run_barge(0.0, seq, cal) is False


def test_barge_ignores_subthreshold_ambient() -> None:
    cal = _cal()
    assert _run_barge(0.0, [0.05] * 40, cal) is False  # 0.05 < barge_threshold 0.15
