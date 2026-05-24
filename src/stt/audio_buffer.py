"""Ring buffer — WebSocket'ten gelen PCM chunk'larını toplar.

Whisper bütün konuşma segmentini alıp tek seferde transcribe eder. Bu yüzden
chunk'ları VAD'in "konuşma bitti" sinyali gelene kadar biriktirip topluca
göndeririz. Maksimum süre aşılırsa eski sample'lar düşer (yarım saatlik
buffer fest gecesinde belleği patlatmasın).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, PCM_SAMPLE_WIDTH, pcm_bytes_to_numpy


class AudioBuffer:
    """16 kHz mono PCM için kayan pencere buffer.

    Yöntemler senkron — append HTTP/WS event loop'unda çağrılır, yan etkisi
    sadece bellek ekleme/silme. Whisper'a verirken ``get_audio_numpy`` çağır.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        max_duration_seconds: int = 30,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        self.sample_rate = sample_rate
        self.max_samples = sample_rate * max_duration_seconds
        self._chunks: list[NDArray[np.float32]] = []
        self._total_samples: int = 0

    def append(self, chunk: bytes) -> None:
        """PCM byte chunk ekle. Sınırı aşarsa baştan düş."""
        if not chunk:
            return
        audio = pcm_bytes_to_numpy(chunk, sample_width=PCM_SAMPLE_WIDTH)
        if audio.size == 0:
            return
        self._chunks.append(audio)
        self._total_samples += audio.size
        self._trim_to_max()

    def append_numpy(self, audio: NDArray[np.float32]) -> None:
        """float32 array ekle (mikrofon callback için)."""
        if audio.size == 0:
            return
        self._chunks.append(audio.astype(np.float32, copy=False))
        self._total_samples += audio.size
        self._trim_to_max()

    def get_audio_numpy(self) -> NDArray[np.float32]:
        """Buffer'daki tüm sample'ları tek float32 array olarak döndür."""
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._total_samples = 0

    def duration_ms(self) -> int:
        return int(self._total_samples * 1000 / self.sample_rate)

    def sample_count(self) -> int:
        return self._total_samples

    def __len__(self) -> int:  # bayt değil sample sayısı
        return self._total_samples

    # ------------------------------------------------------------------

    def _trim_to_max(self) -> None:
        while self._total_samples > self.max_samples and self._chunks:
            first = self._chunks[0]
            excess = self._total_samples - self.max_samples
            if first.size <= excess:
                self._chunks.pop(0)
                self._total_samples -= first.size
            else:
                self._chunks[0] = first[excess:]
                self._total_samples -= excess


__all__ = ["AudioBuffer"]
