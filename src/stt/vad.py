"""Voice Activity Detection — Silero VAD wrapper + RMS fallback.

Silero VAD 16 kHz / 512 sample (32 ms) chunk bekler. Bu sınıf gelen chunk'ları
512 sample'lık pencerelere keser, her pencereyi model'e yedirir, çıktı
olasılığını hysteresis ile speech_start / speech_end event'ine çevirir.

Torch yoksa veya yüklenmezse RMSFallbackVAD devreye girer — sergiyi durdurmaz.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional, Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, is_silent

if TYPE_CHECKING:  # pragma: no cover
    import torch

_log = logging.getLogger(__name__)

SILERO_CHUNK = 512  # 32 ms @ 16 kHz — Silero VAD'in kabul ettiği sabit boyut


class VADResult(BaseModel):
    speech_detected: bool
    is_speech_start: bool
    is_speech_end: bool
    speech_probability: float
    timestamp_ms: int


class VADBackend(Protocol):
    def predict(self, audio: NDArray[np.float32]) -> float:
        ...


class SileroBackend:
    """Silero VAD model wrapper (lazy load — torch import edilince yüklenir)."""

    def __init__(self) -> None:
        # torch ve silero-vad pip extra "stt" ile gelir; yoksa caller fallback'e döner.
        import torch  # noqa: F401  (re-import in load)
        from silero_vad import load_silero_vad  # type: ignore[import-not-found]

        self.model: Any = load_silero_vad()
        self.model.eval()
        self._torch = __import__("torch")

    def predict(self, audio: NDArray[np.float32]) -> float:
        if audio.size != SILERO_CHUNK:
            raise ValueError(f"Silero {SILERO_CHUNK} sample bekler, geldi {audio.size}")
        with self._torch.no_grad():
            tensor = self._torch.from_numpy(audio.astype(np.float32, copy=False))
            prob = self.model(tensor, DEFAULT_SAMPLE_RATE).item()
        return float(prob)


class RMSFallbackBackend:
    """Torch yoksa kullanılır. RMS eşiği üstünde → konuşma."""

    def __init__(self, threshold_rms: float = 0.01) -> None:
        self.threshold_rms = threshold_rms

    def predict(self, audio: NDArray[np.float32]) -> float:
        if is_silent(audio, threshold_rms=self.threshold_rms):
            return 0.0
        return 1.0


class VoiceActivityDetector:
    """Chunk-by-chunk VAD durum makinesi.

    Hysteresis: ``threshold`` üstüne çıkınca speech başlar, sonrasında
    ``min_silence_duration_ms`` boyunca aşağıda kalırsa biter.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 800,
        backend: Optional[VADBackend] = None,
    ) -> None:
        if sample_rate != DEFAULT_SAMPLE_RATE:
            raise ValueError("Silero VAD yalnızca 16 kHz destekler")
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_samples = int(min_speech_duration_ms * sample_rate / 1000)
        self.min_silence_samples = int(min_silence_duration_ms * sample_rate / 1000)
        self.backend: VADBackend = backend or self._make_backend()
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._timestamp = 0  # işlenmiş sample sayısı
        self._last_prob = 0.0

    @staticmethod
    def _make_backend() -> VADBackend:
        try:
            return SileroBackend()
        except Exception as exc:  # noqa: BLE001
            _log.warning("silero_vad_unavailable", extra={"error": str(exc)})
            return RMSFallbackBackend()

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._timestamp = 0
        self._last_prob = 0.0

    def process_chunk(self, audio_chunk: NDArray[np.float32]) -> list[VADResult]:
        """Bir audio dilimi yedir; oluşan event'leri döndür.

        Bir chunk'ta birden fazla 512-sample pencere düşebilir, hepsinin
        sonucu ayrı VADResult olarak gelir.
        """
        if audio_chunk.size == 0:
            return []
        merged = (
            np.concatenate([self._pending, audio_chunk])
            if self._pending.size
            else audio_chunk.astype(np.float32, copy=False)
        )

        results: list[VADResult] = []
        idx = 0
        while merged.size - idx >= SILERO_CHUNK:
            window = merged[idx : idx + SILERO_CHUNK]
            idx += SILERO_CHUNK
            prob = self.backend.predict(window)
            self._last_prob = prob
            self._timestamp += SILERO_CHUNK
            results.append(self._advance(prob))

        self._pending = merged[idx:]
        return results

    # ------------------------------------------------------------------

    def _advance(self, prob: float) -> VADResult:
        active = prob >= self.threshold
        is_start = False
        is_end = False

        if active:
            self._silence_run = 0
            self._speech_run += SILERO_CHUNK
            if not self._in_speech and self._speech_run >= self.min_speech_samples:
                self._in_speech = True
                is_start = True
        else:
            self._speech_run = 0
            self._silence_run += SILERO_CHUNK
            if self._in_speech and self._silence_run >= self.min_silence_samples:
                self._in_speech = False
                is_end = True

        return VADResult(
            speech_detected=self._in_speech,
            is_speech_start=is_start,
            is_speech_end=is_end,
            speech_probability=prob,
            timestamp_ms=int(self._timestamp * 1000 / self.sample_rate),
        )


__all__ = [
    "VADResult",
    "VoiceActivityDetector",
    "SileroBackend",
    "RMSFallbackBackend",
    "VADBackend",
    "SILERO_CHUNK",
]
