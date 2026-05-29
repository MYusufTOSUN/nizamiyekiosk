"""XTTS TTS sentez testi.

Kullanım:
    python scripts/test_tts.py "Aleyküm selam evladım"
    python scripts/test_tts.py "..." --out cezeri_test.wav      # dosyaya yaz
    python scripts/test_tts.py "..." --voice cezeri             # voice_id
    python scripts/test_tts.py "..." --play                     # PC hoparlöründen çal
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from src.core.config import get_config
from src.tts.xtts_local import XTTS_NATIVE_SR, XTTSConfig, XTTSLocalTTS


async def main(text: str, voice_id: str, out_path: str | None, play: bool) -> int:
    try:
        import numpy as np
    except ImportError:
        print("HATA: numpy yok.")
        return 1

    cfg = get_config()
    tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    print("XTTS yukleniyor...")
    await tts._ensure_model()

    print(f"Sentez: '{text}' voice={voice_id}")
    pcm_buffer = bytearray()
    start = time.perf_counter()
    first_chunk_ms = None
    async for chunk in tts.synthesize_stream(text, voice_id):
        if first_chunk_ms is None:
            first_chunk_ms = int((time.perf_counter() - start) * 1000)
        pcm_buffer.extend(chunk)
    total_ms = int((time.perf_counter() - start) * 1000)

    audio = np.frombuffer(bytes(pcm_buffer), dtype=np.int16).astype(np.float32) / 32768.0
    duration_s = audio.size / XTTS_NATIVE_SR
    rtf = (total_ms / 1000.0) / max(duration_s, 1e-3)
    print(
        f"[OK] first_chunk={first_chunk_ms}ms total={total_ms}ms "
        f"audio={duration_s:.2f}s RTF={rtf:.2f}"
    )

    if out_path:
        try:
            import soundfile as sf  # type: ignore[import-not-found]

            sf.write(out_path, audio, XTTS_NATIVE_SR, subtype="PCM_16")
            print(f"WAV yazildi: {out_path}")
        except ImportError:
            print("HATA: soundfile yok, dosyaya yazilamadi.")

    if play:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            print("Oynatiliyor...")
            sd.play(audio, samplerate=XTTS_NATIVE_SR, blocking=True)
        except ImportError:
            print("HATA: sounddevice yok, oynatilamadi.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BilimFest TTS canli test")
    parser.add_argument("text", help="Sentezlenecek metin")
    parser.add_argument("--voice", default="cezeri", help="Voice id (klasör adı)")
    parser.add_argument("--out", default=None, help="WAV çıktı dosyası")
    parser.add_argument("--play", action="store_true", help="PC hoparloruyle çal")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.text, args.voice, args.out, args.play)))
