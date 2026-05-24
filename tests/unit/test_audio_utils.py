"""PCM ↔ numpy dönüşüm ve resample testleri."""

from __future__ import annotations

import numpy as np
import pytest

from src.stt.audio_utils import (
    is_silent,
    numpy_to_pcm_bytes,
    pcm_bytes_to_numpy,
    resample,
    rms,
)


def test_pcm_roundtrip_preserves_signal() -> None:
    rng = np.random.default_rng(42)
    audio = rng.uniform(-0.9, 0.9, size=480).astype(np.float32)
    encoded = numpy_to_pcm_bytes(audio)
    decoded = pcm_bytes_to_numpy(encoded)
    assert decoded.shape == audio.shape
    # 16-bit quantization tolerance
    np.testing.assert_allclose(decoded, audio, atol=1e-3)


def test_pcm_to_numpy_empty() -> None:
    out = pcm_bytes_to_numpy(b"")
    assert out.dtype == np.float32
    assert out.size == 0


def test_pcm_to_numpy_odd_bytes_truncates() -> None:
    # 3 byte (geçersiz, son byte düşmeli)
    out = pcm_bytes_to_numpy(b"\x00\x00\x7f")
    assert out.size == 1


def test_pcm_to_numpy_clipping_safe_range() -> None:
    # 0x7FFF → +1.0'a yakın, -0x8000 → -1.0
    bytes_hi = (np.array([32767], dtype=np.int16)).tobytes()
    bytes_lo = (np.array([-32768], dtype=np.int16)).tobytes()
    assert pcm_bytes_to_numpy(bytes_hi)[0] == pytest.approx(0.9999, abs=1e-3)
    assert pcm_bytes_to_numpy(bytes_lo)[0] == pytest.approx(-1.0, abs=1e-3)


def test_numpy_to_pcm_bytes_clips_overflow() -> None:
    audio = np.array([2.0, -2.0], dtype=np.float32)
    encoded = numpy_to_pcm_bytes(audio)
    decoded = pcm_bytes_to_numpy(encoded)
    assert decoded[0] == pytest.approx(0.9999, abs=1e-3)
    assert decoded[1] == pytest.approx(-1.0, abs=1e-3)


def test_resample_identity() -> None:
    audio = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    out = resample(audio, 16000, 16000)
    np.testing.assert_array_equal(out, audio)


def test_resample_upsample() -> None:
    audio = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    out = resample(audio, 16000, 32000)
    assert out.size == 200
    assert out.dtype == np.float32


def test_resample_downsample() -> None:
    audio = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    out = resample(audio, 16000, 8000)
    assert out.size == 50


def test_rms_and_silence() -> None:
    silent = np.zeros(160, dtype=np.float32)
    loud = np.full(160, 0.5, dtype=np.float32)
    assert rms(silent) == 0.0
    assert rms(loud) == pytest.approx(0.5, abs=1e-6)
    assert is_silent(silent)
    assert not is_silent(loud)


def test_rms_empty() -> None:
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0
