"""VAD durum makinesi testleri — RMS backend ile (torch zorunlu değil)."""

from __future__ import annotations

import numpy as np

from src.stt.vad import (
    SILERO_CHUNK,
    RMSFallbackBackend,
    VoiceActivityDetector,
)


def make_silence(n_chunks: int) -> np.ndarray:
    return np.zeros(n_chunks * SILERO_CHUNK, dtype=np.float32)


def make_speech(n_chunks: int, amplitude: float = 0.5) -> np.ndarray:
    # Sin wave 200 Hz @ 16 kHz — RMS > 0.01 garanti
    samples = n_chunks * SILERO_CHUNK
    t = np.arange(samples, dtype=np.float32) / 16000.0
    return (amplitude * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


def test_rms_backend_detects_speech_and_silence() -> None:
    backend = RMSFallbackBackend(threshold_rms=0.01)
    assert backend.predict(make_speech(1)) == 1.0
    assert backend.predict(make_silence(1)) == 0.0


def test_speech_start_after_min_duration() -> None:
    vad = VoiceActivityDetector(
        threshold=0.5,
        min_speech_duration_ms=64,  # 2 chunk @ 32ms
        min_silence_duration_ms=64,
        backend=RMSFallbackBackend(),
    )
    # 1 chunk konuşma — daha min_speech'e ulaşmadı
    results = vad.process_chunk(make_speech(1))
    assert len(results) == 1
    assert results[0].is_speech_start is False
    # 2. chunk konuşma — şimdi başlamalı
    results = vad.process_chunk(make_speech(1))
    assert results[0].is_speech_start is True
    assert results[0].speech_detected is True


def test_speech_end_after_silence_min() -> None:
    vad = VoiceActivityDetector(
        threshold=0.5,
        min_speech_duration_ms=32,
        min_silence_duration_ms=96,  # 3 chunk
        backend=RMSFallbackBackend(),
    )
    vad.process_chunk(make_speech(2))  # speech_start
    # 2 chunk silence — henüz end yok
    r = vad.process_chunk(make_silence(2))
    assert all(not x.is_speech_end for x in r)
    # 1 daha chunk silence → end
    r = vad.process_chunk(make_silence(1))
    assert any(x.is_speech_end for x in r)
    assert vad._in_speech is False  # noqa: SLF001


def test_multiple_windows_in_one_chunk() -> None:
    vad = VoiceActivityDetector(
        threshold=0.5,
        min_speech_duration_ms=32,
        min_silence_duration_ms=32,
        backend=RMSFallbackBackend(),
    )
    big = make_speech(5)  # 5 silero pencere
    results = vad.process_chunk(big)
    assert len(results) == 5


def test_pending_audio_carries_over() -> None:
    vad = VoiceActivityDetector(
        threshold=0.5,
        min_speech_duration_ms=32,
        min_silence_duration_ms=32,
        backend=RMSFallbackBackend(),
    )
    half = SILERO_CHUNK // 2
    # Tek başına 256 sample → henüz işlenmez
    r = vad.process_chunk(np.zeros(half, dtype=np.float32))
    assert r == []
    # 256 daha → birleşip 1 pencere oluşturmalı
    r = vad.process_chunk(np.zeros(half, dtype=np.float32))
    assert len(r) == 1


def test_reset_clears_state() -> None:
    vad = VoiceActivityDetector(
        threshold=0.5,
        min_speech_duration_ms=32,
        min_silence_duration_ms=32,
        backend=RMSFallbackBackend(),
    )
    vad.process_chunk(make_speech(2))
    vad.reset()
    assert vad._in_speech is False  # noqa: SLF001
    assert vad._timestamp == 0  # noqa: SLF001


def test_invalid_sample_rate_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        VoiceActivityDetector(sample_rate=8000)
