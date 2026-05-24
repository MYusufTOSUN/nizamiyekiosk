"""AudioBuffer testleri — append, clear, duration, overflow."""

from __future__ import annotations

import numpy as np
import pytest

from src.stt.audio_buffer import AudioBuffer
from src.stt.audio_utils import numpy_to_pcm_bytes


@pytest.fixture
def buf() -> AudioBuffer:
    return AudioBuffer(sample_rate=16000, max_duration_seconds=1)


def test_initially_empty(buf: AudioBuffer) -> None:
    assert buf.duration_ms() == 0
    assert buf.sample_count() == 0
    assert buf.get_audio_numpy().size == 0


def test_append_chunk(buf: AudioBuffer) -> None:
    audio = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)  # 100ms @ 16kHz
    buf.append(numpy_to_pcm_bytes(audio))
    assert buf.sample_count() == 1600
    assert buf.duration_ms() == 100


def test_append_numpy(buf: AudioBuffer) -> None:
    audio = np.zeros(800, dtype=np.float32)
    buf.append_numpy(audio)
    assert buf.duration_ms() == 50


def test_append_empty_bytes_noop(buf: AudioBuffer) -> None:
    buf.append(b"")
    assert buf.duration_ms() == 0


def test_clear_resets(buf: AudioBuffer) -> None:
    buf.append_numpy(np.zeros(1600, dtype=np.float32))
    buf.clear()
    assert buf.duration_ms() == 0
    assert buf.get_audio_numpy().size == 0


def test_overflow_drops_oldest_samples() -> None:
    buf = AudioBuffer(sample_rate=16000, max_duration_seconds=1)  # max 16000 sample
    # 2 saniyelik veri ekle, max 1 saniye kalmalı
    buf.append_numpy(np.full(16000, 0.1, dtype=np.float32))  # eski
    buf.append_numpy(np.full(16000, 0.9, dtype=np.float32))  # yeni
    audio = buf.get_audio_numpy()
    assert audio.size == 16000
    # Yeni (0.9) dolu olmalı; eski (0.1) düşmeli
    assert np.allclose(audio, 0.9, atol=1e-5)


def test_partial_overflow_drop() -> None:
    buf = AudioBuffer(sample_rate=16000, max_duration_seconds=1)  # max 16000 sample
    buf.append_numpy(np.full(10000, 0.1, dtype=np.float32))
    buf.append_numpy(np.full(10000, 0.9, dtype=np.float32))
    audio = buf.get_audio_numpy()
    assert audio.size == 16000
    # Toplam 20000 sample geldi, 4000 sample düştü → 6000×0.1 + 10000×0.9
    assert np.allclose(audio[:6000], 0.1, atol=1e-5)
    assert np.allclose(audio[6000:], 0.9, atol=1e-5)


def test_invalid_sample_rate() -> None:
    with pytest.raises(ValueError):
        AudioBuffer(sample_rate=0)


def test_invalid_max_duration() -> None:
    with pytest.raises(ValueError):
        AudioBuffer(max_duration_seconds=0)


def test_len_returns_sample_count(buf: AudioBuffer) -> None:
    buf.append_numpy(np.zeros(123, dtype=np.float32))
    assert len(buf) == 123
