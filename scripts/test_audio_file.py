"""Bir .wav dosyasını WhisperLocalSTT'ye yedir, transcript'i bas.

Kullanım:
    python scripts/test_audio_file.py path/to/audio.wav
    python scripts/test_audio_file.py path/to/audio.wav --language tr
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes, resample
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT


def _load_wav(path: Path):
    try:
        import soundfile as sf  # type: ignore[import-not-found]
    except ImportError:
        print("HATA: soundfile kurulu değil.")
        sys.exit(1)

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != DEFAULT_SAMPLE_RATE:
        audio = resample(audio, sr, DEFAULT_SAMPLE_RATE)
    return audio


async def main(audio_path: Path, language: str) -> int:
    if not audio_path.exists():
        print(f"Dosya yok: {audio_path}")
        return 1

    audio = _load_wav(audio_path)
    pcm_bytes = numpy_to_pcm_bytes(audio)

    # 20 ms chunk'lara böl (WebSocket pipeline ile aynı format)
    chunk_size = 320 * 2  # 320 sample × 2 byte
    chunks = [pcm_bytes[i : i + chunk_size] for i in range(0, len(pcm_bytes), chunk_size)]

    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    stt = WhisperLocalSTT(WhisperConfig(language=language, flush_on_stream_end=True))
    start = time.perf_counter()
    async for result in stt.transcribe_stream(stream(), language=language):
        latency = int((time.perf_counter() - start) * 1000)
        print(f"[{latency} ms] {result.text}  (conf={result.confidence:.2f})")
    print(f"Toplam {int((time.perf_counter() - start) * 1000)} ms")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WAV → Whisper transcript")
    parser.add_argument("path", type=Path, help="WAV dosyası")
    parser.add_argument("--language", default="tr")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.path, args.language)))
