"""PC mikrofon → WhisperLocalSTT canlı test.

PC sürümü: Pepper's Ghost yok, sadece konuş + transcribe. Çıktıyı konsola
basar, latency'i raporlar. Aynı PCM 16 kHz mono formatı kullanır — sergi
makinesine alındığında WebSocket pipeline'ı ile tıpatıp aynı.

Kullanım:
    python scripts/test_microphone.py [--seconds 8]
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import time
from collections.abc import AsyncIterator

from src.core.config import get_config
from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT

PCM_CHUNK_SAMPLES = 320  # 20 ms @ 16 kHz, api_contracts.md §1


async def main(seconds: float, device: str | int | None) -> int:
    try:
        import numpy as np
        import sounddevice as sd  # type: ignore[import-not-found]
    except ImportError:
        print("HATA: sounddevice/numpy kurulu degil. pip install -e .[stt]")
        return 1

    # config.yaml'dan model path + VAD parametrelerini al — re-download'u önler.
    app_cfg = get_config()
    whisper_cfg = WhisperConfig(**{**app_cfg.stt.config, "flush_on_stream_end": True})

    if device is not None:
        info = sd.query_devices(device, "input")
        print(f"Mikrofon: {info['name']} (index={info['index']})")
    else:
        print("Mikrofon: default")
    print(f"Whisper yukleniyor (model={whisper_cfg.model})...")

    stt = WhisperLocalSTT(whisper_cfg)
    await stt._ensure_model()

    audio_q: queue.Queue[bytes | None] = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        if status:
            print(f"[stream warning] {status}", file=sys.stderr)
        mono = indata[:, 0] if indata.ndim > 1 else indata
        audio_q.put(numpy_to_pcm_bytes(np.asarray(mono, dtype=np.float32)))

    async def audio_iter() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, audio_q.get)
            if chunk is None:
                return
            yield chunk

    with sd.InputStream(
        samplerate=DEFAULT_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=PCM_CHUNK_SAMPLES,
        device=device,
        callback=callback,
    ):
        print(f"KONUŞ ({seconds:.1f}s)…")
        start = time.perf_counter()

        async def stop_after() -> None:
            await asyncio.sleep(seconds)
            audio_q.put(None)

        stop_task = asyncio.create_task(stop_after())
        try:
            async for result in stt.transcribe_stream(audio_iter()):
                latency = int((time.perf_counter() - start) * 1000)
                print(
                    f"[{latency:>5} ms] {result.text}  "
                    f"(conf={result.confidence:.2f}, dur={result.duration_ms}ms)"
                )
        finally:
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass

    print("Bitti.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mikrofon → Whisper canlı test")
    parser.add_argument("--seconds", type=float, default=8.0, help="Kayıt süresi (s)")
    parser.add_argument(
        "--device",
        default=None,
        help="sounddevice giriş cihazı (index veya isim). Boş = varsayılan.",
    )
    args = parser.parse_args()
    device: str | int | None = args.device
    if device is not None:
        try:
            device = int(device)
        except ValueError:
            pass
    sys.exit(asyncio.run(main(args.seconds, device)))
