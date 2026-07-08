"""faster-whisper tabanlı lokal STT provider.

Pipeline: PCM chunk → AudioBuffer + VAD → speech bitince Whisper sync çağrısı
(thread pool'da, async loop bloklanmasın) → TranscriptionResult.

Festival makinesinde CUDA üstünde INT8/FP16 mix. Laptop GPU 8 GB için bile
yeterli (3-4 GB).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from src.core.errors import STTError
from src.core.interfaces import STTProvider, TranscriptionResult
from src.core.logger import get_logger
from src.core.metrics import stt_latency_ms
from src.stt.audio_buffer import AudioBuffer
from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, pcm_bytes_to_numpy, rms
from src.stt.vad import VoiceActivityDetector

# Whisper sıkça uydurduğu altyazı/credit cümleleri. Tam eşleşmede sus.
HALLUCINATION_BLOCKLIST: frozenset[str] = frozenset(
    s.lower().strip()
    for s in (
        "altyazı m.k.",
        "altyazı: m.k.",
        "altyazi m.k.",
        "altyazı m.k. ile çevrildi",
        "altyazılar",
        "alt yazı",
        "abone ol",
        "abone olmayı unutmayın",
        "teşekkürler",
        "izlediğiniz için teşekkürler",
        "izlediğiniz için teşekkür ederim",
        "videolarımıza abone olun",
        "kanalımıza abone olun",
        "altyazı: hayrettin g.",
        "altyazı: ümit özdemir",
    )
)

_HALLUCINATION_STRIP_CHARS = ' \t\n\r.!?:,;"\''

# Gömülü/birleşik varyant katmanı — Whisper credit'i gerçek/uydurma metinle
# BİRLEŞTİRİR ("İzlediğiniz için teşekkür ederim arkadaşlar", "Altyazı M.K. Hoşça
# kalın"); bunlar tam-eşleşmez. YALNIZ yüksek-spesifik, ayırt edici alt-dizeler:
# "teşekkürler" gibi tek-kelime jenerikleri BURAYA KOYMA (gerçek çocuk diyebilir).
_HALLUCINATION_SUBSTRINGS: tuple[str, ...] = (
    "altyazı m.k", "altyazi m.k", "m.k. ile çevrildi",
    "abone ol", "abone olmayı unutma", "kanalımıza abone", "kanalıma abone",
    "videolarımıza abone", "beğenmeyi unutma",
    "izlediğiniz için teşekkür",
)


def _tr_lower(text: str) -> str:
    """Lowercase + Türkçe 'İ' kaynaklı combining-dot (U+0307) temizliği.

    Python ``"İ".lower()`` → "i̇" (i + U+0307 combining dot) üretir; bu, düz 'i'
    içeren blocklist girişleriyle eşleşmeyi bozar. Combining dot'u silip
    normalize ederiz."""
    return text.lower().replace("̇", "")


def _normalize_for_blocklist(text: str) -> str:
    """Lowercase + trim leading/trailing punctuation + collapse whitespace.

    Whisper "ALTYAZI: M.K." / "altyazı m.k." / " Altyazi M.K. " hepsini aynı
    nokta'ya indirir — case + whitespace + noktalama varyasyonlarına dayanıklı.
    """
    return " ".join(_tr_lower(text).strip(_HALLUCINATION_STRIP_CHARS).split())


def _normalize_for_substring(text: str) -> str:
    """Substring katmanı için: lower + whitespace collapse (iç noktalama korunur)."""
    return " ".join(_tr_lower(text).split())


_HALLUCINATION_NORMALIZED: frozenset[str] = frozenset(
    _normalize_for_blocklist(s) for s in HALLUCINATION_BLOCKLIST
)


def _is_hallucination(text: str) -> bool:
    # 1) tam-eşleşme (yanlış-pozitif düşük)
    if _normalize_for_blocklist(text) in _HALLUCINATION_NORMALIZED:
        return True
    # 2) yüksek-spesifik gömülü alt-dize (credit metni başka kelimelerle birleşmiş)
    sub = _normalize_for_substring(text)
    return any(s in sub for s in _HALLUCINATION_SUBSTRINGS)


_log = get_logger(component="stt.whisper")


class WhisperConfig(BaseModel):
    model: str = "large-v3"
    language: str = "tr"
    device: str = "cuda"  # "cuda" | "cpu"
    compute_type: str = "int8_float16"  # bkz. faster-whisper README
    model_dir: str = "data/models/whisper"
    cpu_threads: int = 4
    num_workers: int = 1

    sample_rate: int = DEFAULT_SAMPLE_RATE
    beam_size: int = 5
    best_of: int = 5
    # SICAKLIK FALLBACK (atipik/zor konuşma için KRİTİK): tek 0.0 yerine artan
    # sıcaklık listesi. Greedy decode (0.0) başarısız olursa (yüksek sıkıştırma
    # oranı ya da düşük logprob) Whisper bir sonraki sıcaklıkla TEKRAR dener —
    # aksanlı/hızlı/yutarak/kısık konuşanda saçma çıktıyı toparlar. Tek float
    # verilirse fallback DEVRE DIŞI kalır.
    temperature: float | list[float] = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    # Tekrarlı/uydurma çıktıyı (compression_ratio bunun üstündeyse) reddet → bir üst
    # sıcaklığa fallback tetikler. faster-whisper varsayılanı 2.4.
    compression_ratio_threshold: float = 2.4
    # SES-SEVİYESİ NORMALİZASYONU: kısık/uzak ya da çok yüksek konuşanı aynı tepe
    # seviyesine getir (Whisper atipik seviyede daha az saçmalar). RMS-kapısı sessiz
    # buffer'ı zaten eler; bu yalnız gerçek konuşmayı eşitler.
    loudness_normalize: bool = True
    target_peak: float = 0.97
    # Decoder'ı domain sözlüğüne yönlendir (isim/terim algısını artırır). Çocuklar
    # "Cezeri", "fil saati", "El-Harezmî" gibi kelimeleri net telaffuz edemez;
    # initial_prompt Whisper'a beklenen bağlamı verir. Boş => kullanılmaz.
    # Aşırı uzun/isim-dolu prompt sessizlikte halüsinasyon riskini artırır — ölçülü tut.
    initial_prompt: str = ""

    # VAD ayarları
    vad_threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 800
    max_segment_duration_seconds: int = 30  # bu süreyi aşan tek segmentte zorla flush
    min_segment_duration_ms: int = 500       # bu kadarın altındaysa segment yutma

    # Halüsinasyon savunması — Whisper sessizliğe/uğultuya altyazı credit'i uydurur.
    no_speech_threshold: float = 0.6         # faster-whisper segment no_speech_prob > bu => sus
    min_avg_logprob: float = -1.0            # ortalama logprob düşükse sahte cevap, sus
    min_rms_threshold: float = 0.005         # buffer RMS bunun altındaysa Whisper'ı hiç çağırma
    apply_hallucination_blocklist: bool = True

    # Konsol/test için: speech_end olmadan da gelen final transcript'ler için.
    flush_on_stream_end: bool = True

    model_config = {"extra": "forbid"}


class WhisperLocalSTT(STTProvider):
    """faster-whisper STT — VAD ile segmentleyip Whisper'a yedirir."""

    def __init__(self, config: dict[str, Any] | WhisperConfig | None = None) -> None:
        cfg = self._normalize_config(config)
        self.config = cfg
        self._model_lock = asyncio.Lock()
        self._model: Any | None = None  # lazy load

    @staticmethod
    def _normalize_config(config: dict[str, Any] | WhisperConfig | None) -> WhisperConfig:
        if config is None:
            return WhisperConfig()
        if isinstance(config, WhisperConfig):
            return config
        return WhisperConfig(**config)

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_model_sync)
        return self._model

    def _load_model_sync(self) -> Any:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise STTError(
                "STT_001",
                "faster-whisper kurulu değil. `pip install -e .[stt]` çalıştır.",
                cause=exc,
            ) from exc

        cfg = self.config
        Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
        _log.info(
            "loading_whisper",
            model=cfg.model,
            device=cfg.device,
            compute_type=cfg.compute_type,
        )
        try:
            return WhisperModel(
                model_size_or_path=cfg.model,
                device=cfg.device,
                compute_type=cfg.compute_type,
                cpu_threads=cfg.cpu_threads,
                num_workers=cfg.num_workers,
                download_root=cfg.model_dir,
            )
        except Exception as exc:  # noqa: BLE001
            raise STTError("STT_001", f"Whisper yüklenemedi: {exc}", cause=exc) from exc

    async def warmup(self) -> int:
        """Modeli ONCEDEN yukle + ilk transcribe JIT/cuDNN cezasini yut.

        Startup'ta (XTTS warmup'tan SONRA) cagrilirsa ilk ziyaretci cold
        model-load + kernel JIT yasamaz. Donus: warmup suresi (ms).
        """
        import time as _t

        start = _t.perf_counter()
        await self._ensure_model()
        try:
            silent = np.zeros(int(self.config.sample_rate * 0.5), dtype=np.float32)
            segs, _info = await asyncio.to_thread(
                self._model.transcribe,
                silent,
                language=self.config.language,
                beam_size=1,
            )
            for _ in segs:  # generator'i tuket -> gercek inference
                pass
        except Exception as exc:  # noqa: BLE001
            _log.warning("stt_warmup_inference_failed", error=str(exc))
        return int((_t.perf_counter() - start) * 1000)

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "tr",
    ) -> AsyncIterator[TranscriptionResult]:
        cfg = self.config
        # Buffer'a headroom ver: aksi halde ring-trim tam max_segment'te kesip
        # zorla-flush koşulunu (duration >= max_segment) hiç tetiklemez,
        # uzun monologda cümle başı sessizce düşerdi.
        buffer = AudioBuffer(
            sample_rate=cfg.sample_rate,
            max_duration_seconds=cfg.max_segment_duration_seconds + 5,
        )
        vad = VoiceActivityDetector(
            sample_rate=cfg.sample_rate,
            threshold=cfg.vad_threshold,
            min_speech_duration_ms=cfg.min_speech_duration_ms,
            min_silence_duration_ms=cfg.min_silence_duration_ms,
        )
        await self._ensure_model()

        async for chunk in audio_stream:
            if not chunk:
                continue
            audio_np = pcm_bytes_to_numpy(chunk)
            buffer.append_numpy(audio_np)
            events = vad.process_chunk(audio_np)
            for event in events:
                if (
                    event.is_speech_end
                    and buffer.duration_ms() >= cfg.min_segment_duration_ms
                ):
                    result = await self._transcribe_buffer(buffer, language)
                    if result is not None:
                        yield result
                    buffer.clear()
                    vad.reset()

            # Maksimum süre koruması — VAD takılırsa
            if buffer.duration_ms() >= cfg.max_segment_duration_seconds * 1000:
                result = await self._transcribe_buffer(buffer, language)
                if result is not None:
                    yield result
                buffer.clear()
                vad.reset()

        # Akış DOĞAL bittiğinde (audio_iter None döndürdü) kalan buffer'ı flush et.
        # NOT: bilerek `finally` DEĞİL. Tüketici ilk sonuçta `break` edip generator'ı
        # kapatırsa, `finally` içindeki `yield` GeneratorExit sırasında tetiklenir ve
        # "async generator ignored GeneratorExit" + aynı sesin İKİ kez işlenmesine yol
        # açar (canlı testte görüldü). Flush'ı normal akışa alınca close temiz geçer.
        if cfg.flush_on_stream_end and buffer.duration_ms() >= cfg.min_segment_duration_ms:
            result = await self._transcribe_buffer(buffer, language)
            if result is not None:
                yield result

    async def _transcribe_buffer(
        self,
        buffer: AudioBuffer,
        language: str,
    ) -> TranscriptionResult | None:
        audio_np = buffer.get_audio_numpy()
        duration_ms = int(audio_np.size * 1000 / self.config.sample_rate)
        if audio_np.size == 0:
            return None

        # RMS pre-check — sessiz buffer'da Whisper'ı hiç çağırma (halüsinasyon önler).
        level = rms(audio_np)
        if level < self.config.min_rms_threshold:
            _log.debug(
                "skip_silent_buffer",
                rms=level,
                threshold=self.config.min_rms_threshold,
                duration_ms=duration_ms,
            )
            return None

        # SES-SEVİYESİ NORMALİZASYONU — kısık/uzak ya da yüksek konuşanı eşitle.
        audio_np = self._normalize_loudness(audio_np)

        start = time.perf_counter()
        try:
            text, confidence = await asyncio.to_thread(
                self._whisper_call_sync, audio_np, language
            )
        except STTError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise STTError("STT_001", f"Whisper transcribe hatası: {exc}", cause=exc) from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        stt_latency_ms.observe(latency_ms)

        text = text.strip()
        if not text:
            _log.debug("empty_transcription", duration_ms=duration_ms, latency_ms=latency_ms)
            return None

        if (
            self.config.apply_hallucination_blocklist
            and _is_hallucination(text)
        ):
            _log.info(
                "hallucination_filtered",
                text=text,
                duration_ms=duration_ms,
                latency_ms=latency_ms,
            )
            return None

        _log.info(
            "transcription",
            text_preview=text[:80],
            duration_ms=duration_ms,
            latency_ms=latency_ms,
            confidence=confidence,
        )
        return TranscriptionResult(
            text=text,
            is_final=True,
            confidence=confidence,
            duration_ms=latency_ms,
        )

    def _normalize_loudness(self, audio: Any) -> Any:
        """Tepe seviyesini target_peak'e ölçekle (kısık konuşanı yükselt, yükseği
        kıs). RMS-kapısını geçen GERÇEK konuşmaya uygulanır; sessizi yükseltmez."""
        if not self.config.loudness_normalize or audio.size == 0:
            return audio
        peak = float(np.abs(audio).max())
        if peak < 1e-4:  # neredeyse sessiz — dokunma (gürültüyü yükseltme)
            return audio
        scaled = audio * (self.config.target_peak / peak)
        return np.clip(scaled, -1.0, 1.0).astype(np.float32)

    def _whisper_call_sync(
        self,
        audio: Any,
        language: str,
    ) -> tuple[str, float]:
        if self._model is None:
            raise STTError("STT_001", "Whisper model henüz yüklenmedi")
        segments_iter, info = self._model.transcribe(
            audio,
            language=language or self.config.language,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            temperature=self.config.temperature,  # liste → sıcaklık fallback aktif
            compression_ratio_threshold=self.config.compression_ratio_threshold,
            vad_filter=False,  # bizim VAD zaten segmentledi
            condition_on_previous_text=False,
            initial_prompt=self.config.initial_prompt or None,
            no_speech_threshold=self.config.no_speech_threshold,
            log_prob_threshold=self.config.min_avg_logprob,
        )
        kept_texts: list[str] = []
        confidences: list[float] = []
        for seg in segments_iter:
            no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0))
            avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
            if no_speech_prob >= self.config.no_speech_threshold:
                continue
            if avg_logprob < self.config.min_avg_logprob:
                continue
            kept_texts.append(seg.text)
            # avg_logprob negatif log-likelihood → exp ile [0,1]'e yaklaştır
            confidences.append(float(np.exp(avg_logprob)) if avg_logprob < 0 else 1.0)

        text = " ".join(kept_texts).strip()
        # Boş/filtrelenmiş segmentte 'transkript güveni' YOK → 0.0 (eski
        # language_probability fallback'i ~0.99 verip yanıltıcıydı). Downstream
        # erken-çıkış eşiği bu değeri kullanır.
        confidence = float(np.mean(confidences)) if confidences else 0.0
        return text, confidence

    async def close(self) -> None:
        # faster-whisper modeli açık tutar — process sonunda otomatik temizlenir.
        self._model = None


__all__ = ["WhisperLocalSTT", "WhisperConfig"]
