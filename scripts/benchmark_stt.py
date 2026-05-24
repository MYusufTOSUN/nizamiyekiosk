"""STT gecikme ölçümü.

`tests/fixtures/audio/*.wav` altındaki örnekleri 10 kez işler, P50/P95/P99
gecikme ile VRAM özetini basar.

Kullanım:
    python scripts/benchmark_stt.py [--runs 10] [--fixtures tests/fixtures/audio]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes, resample
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT


def _load_wav(path: Path):
    import soundfile as sf  # type: ignore[import-not-found]

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != DEFAULT_SAMPLE_RATE:
        audio = resample(audio, sr, DEFAULT_SAMPLE_RATE)
    return audio


async def _measure(stt: WhisperLocalSTT, pcm: bytes) -> int:
    chunk_size = 320 * 2
    chunks = [pcm[i : i + chunk_size] for i in range(0, len(pcm), chunk_size)]

    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    start = time.perf_counter()
    async for _ in stt.transcribe_stream(stream()):
        pass
    return int((time.perf_counter() - start) * 1000)


def _gpu_vram_mb() -> int | None:
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            return int(torch.cuda.memory_allocated() / (1024 * 1024))
    except ImportError:
        return None
    return None


async def main(runs: int, fixtures_dir: Path) -> int:
    files = sorted(fixtures_dir.glob("*.wav"))
    if not files:
        print(f"Ses dosyası yok: {fixtures_dir}")
        print("Bu klasöre 5 Türkçe örnek koy (kısa/orta/uzun/çocuk/gürültülü).")
        return 1

    stt = WhisperLocalSTT(WhisperConfig(flush_on_stream_end=True))
    await stt._ensure_model()
    print(f"Whisper yüklendi. VRAM={_gpu_vram_mb()} MB.")

    fixtures = []
    for path in files:
        audio = _load_wav(path)
        fixtures.append((path.name, numpy_to_pcm_bytes(audio)))

    latencies_total: list[int] = []
    print(f"{'File':<30} {'P50':>6} {'P95':>6} {'P99':>6} {'Max':>6}")
    for name, pcm in fixtures:
        runs_ms = []
        for _ in range(runs):
            runs_ms.append(await _measure(stt, pcm))
        runs_ms.sort()
        p50 = runs_ms[len(runs_ms) // 2]
        p95 = runs_ms[min(len(runs_ms) - 1, int(len(runs_ms) * 0.95))]
        p99 = runs_ms[-1]
        print(f"{name:<30} {p50:>6} {p95:>6} {p99:>6} {max(runs_ms):>6}")
        latencies_total.extend(runs_ms)

    print()
    print(
        f"Toplam {len(latencies_total)} ölçüm — "
        f"P50={statistics.median(latencies_total):.0f}ms "
        f"mean={statistics.mean(latencies_total):.0f}ms"
    )
    print(f"VRAM (transcribe sonrası) = {_gpu_vram_mb()} MB")
    print("Hedef: P95 < 1000ms, P99 < 1500ms.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STT latency benchmark")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures/audio"))
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.runs, args.fixtures)))
