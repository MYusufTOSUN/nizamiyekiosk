"""WhisperLocalSTT gerçek model integration testi.

CUDA + Whisper modeli yoksa skip edilir. Festival/CI'da gpu marker'lı bu test
çalıştırılarak STT'nin sahaya hazır olduğu doğrulanır.

Çalıştırma:
    pytest tests/integration/test_whisper_local.py -v -m gpu
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"


def _has_cuda() -> bool:
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _has_whisper() -> bool:
    try:
        importlib.import_module("faster_whisper")
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not (_has_cuda() and _has_whisper()),
    reason="CUDA + faster-whisper gerekli",
)


@pytest.mark.gpu
@pytest.mark.e2e
async def test_whisper_transcribes_silence_returns_no_results() -> None:
    """Boş ses → yield etmez (en az model yükleniyor mu test eder)."""
    from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT

    stt = WhisperLocalSTT(WhisperConfig(flush_on_stream_end=False))

    async def silent_stream() -> AsyncIterator[bytes]:
        # 1 sn sessizlik (16000 sample × 2 byte = 32000 byte)
        for _ in range(50):
            yield b"\x00" * 640  # 320 sample × 2 byte = 20 ms

    results = []
    async for r in stt.transcribe_stream(silent_stream()):
        results.append(r)
    # VAD speech_end tetiklenmediği için yield yok
    assert results == []


@pytest.mark.gpu
@pytest.mark.e2e
async def test_whisper_transcribes_fixture_audio() -> None:
    """tests/fixtures/audio/*.wav varsa transcribe et."""
    if not FIXTURE_DIR.exists():
        pytest.skip(f"Fixture klasörü yok: {FIXTURE_DIR}")
    wavs = list(FIXTURE_DIR.glob("*.wav"))
    if not wavs:
        pytest.skip(f"Fixture WAV yok: {FIXTURE_DIR}")

    import soundfile as sf  # type: ignore[import-not-found]

    from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes, resample
    from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT

    audio, sr = sf.read(str(wavs[0]), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != DEFAULT_SAMPLE_RATE:
        audio = resample(audio, sr, DEFAULT_SAMPLE_RATE)
    pcm = numpy_to_pcm_bytes(audio)

    chunk_size = 640  # 20 ms
    chunks = [pcm[i : i + chunk_size] for i in range(0, len(pcm), chunk_size)]

    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    stt = WhisperLocalSTT(WhisperConfig(flush_on_stream_end=True))
    results = []
    async for r in stt.transcribe_stream(stream()):
        results.append(r)

    assert results, "En az bir transcription gelmeli"
    assert all(r.text.strip() for r in results)
