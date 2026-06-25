"""M13: RAG cevaplarını TTS cache'e ısıt (sergi öncesi bir kez).

XTTS streaming Coqui+transformers 4.46 bug'ı yüzünden kapalı → first-byte yalnız
cache hit'te düşük. Bu script tüm RAG cevaplarını önceden sentezleyip
``data/tts_cache/`` altına yazar. Sergide RAG hit alan her cevap diskten
~30 ms'de stream edilir, XTTS hiç çağrılmaz.

Kullanım:
    python scripts/prewarm_tts_cache.py                  # tüm responses/*.json
    python scripts/prewarm_tts_cache.py cezeri           # tek persona
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from src.core.config import get_config
from src.llm.persona import get_persona
from src.tts.xtts_local import XTTSConfig, XTTSLocalTTS

RESPONSES_DIR = Path("src/llm/responses")


async def prewarm(personas: list[str]) -> int:
    cfg = get_config()
    tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    print("XTTS yukleniyor...")
    await tts._ensure_model()

    files: list[Path]
    if personas:
        files = [RESPONSES_DIR / f"{p}.json" for p in personas]
    else:
        files = sorted(RESPONSES_DIR.glob("*.json"))

    total = 0
    cached = 0
    start = time.perf_counter()
    for jf in files:
        if not jf.exists():
            print(f"ATLA: {jf} yok")
            continue
        persona_id = jf.stem
        persona = get_persona(persona_id)
        voice_id = persona.voice_id if persona else persona_id
        data = json.loads(jf.read_text(encoding="utf-8"))
        responses = data.get("responses", [])
        print(f"\n{persona_id}: {len(responses)} cevap isiniyor (voice={voice_id})...")
        for i, entry in enumerate(responses):
            text = entry.get("response", "").strip()
            if not text:
                continue
            total += 1
            # synthesize_stream cache'e otomatik yazar; sadece tüketmemiz yeterli
            byte_count = 0
            async for chunk in tts.synthesize_stream(text, voice_id):
                byte_count += len(chunk)
            cached += 1
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(responses)} ...")

    elapsed = time.perf_counter() - start
    print(f"\n[OK] {cached}/{total} cevap cache'e yazildi ({elapsed:.0f}s).")
    # XTTS artik kendi alt-klasorune yazar (saglayici izolasyonu): <cache_dir>/xtts
    root = cfg.tts.config.get("cache_dir", "data/tts_cache")
    print(f"Cache dizini: {root}/xtts")
    await tts.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BilimFest TTS cache prewarm")
    parser.add_argument("personas", nargs="*", help="Persona id'leri (boş = hepsi)")
    args = parser.parse_args()
    return asyncio.run(prewarm(args.personas))


if __name__ == "__main__":
    sys.exit(main())
