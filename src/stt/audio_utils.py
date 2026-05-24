"""Ses formatı dönüşümleri.

API kontratı (api_contracts.md §1): PCM 16-bit signed little-endian, 16 kHz mono.
Whisper float32 numpy [-1, 1] bekliyor. Bu dosyadaki yardımcılar dönüşümleri
yapar — aynı format Phase 7'de Unreal Live Link audio için de geçerli.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

PCM_SAMPLE_WIDTH = 2  # 16-bit
DEFAULT_SAMPLE_RATE = 16000
INT16_MAX = 32768.0


def pcm_bytes_to_numpy(
    pcm_bytes: bytes,
    sample_width: int = PCM_SAMPLE_WIDTH,
) -> NDArray[np.float32]:
    """PCM 16-bit LE bytes → float32 numpy [-1, 1]."""
    if sample_width != 2:
        raise ValueError(f"Sadece 16-bit PCM destekleniyor, sample_width={sample_width}")
    if len(pcm_bytes) == 0:
        return np.zeros(0, dtype=np.float32)
    if len(pcm_bytes) % sample_width != 0:
        # Eksik son sample'ı at — buffer'lı stream'de görülebilir
        pcm_bytes = pcm_bytes[: -(len(pcm_bytes) % sample_width)]
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return (samples.astype(np.float32) / INT16_MAX).astype(np.float32)


def numpy_to_pcm_bytes(audio: NDArray[np.float32]) -> bytes:
    """Float32 numpy [-1, 1] → PCM 16-bit LE bytes."""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * (INT16_MAX - 1)).astype(np.int16).tobytes()


def resample(
    audio: NDArray[np.float32],
    src_rate: int,
    dst_rate: int,
) -> NDArray[np.float32]:
    """Doğrusal interpolasyonla resample (Whisper için 16 kHz hedef).

    scipy.signal.resample yerine basit linear interp tercih ettim çünkü
    küçük chunk'larda scipy'in FFT'si overhead yaratıyor; festival kalite
    farkı duyulmuyor (16 kHz hedef için kaynak da çoğunlukla 16 kHz olur).
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    new_length = int(round(audio.size * dst_rate / src_rate))
    if new_length <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False, dtype=np.float64)
    x_new = np.linspace(0.0, 1.0, new_length, endpoint=False, dtype=np.float64)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def rms(audio: NDArray[np.float32]) -> float:
    """Root-mean-square seviyesi (linear, 0–1)."""
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def is_silent(audio: NDArray[np.float32], threshold_rms: float = 0.005) -> bool:
    """Çok düşük RMS → gerçek konuşma yok (fallback VAD)."""
    return rms(audio) < threshold_rms


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "PCM_SAMPLE_WIDTH",
    "pcm_bytes_to_numpy",
    "numpy_to_pcm_bytes",
    "resample",
    "rms",
    "is_silent",
]
