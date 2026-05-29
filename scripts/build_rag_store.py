"""RAG vector store'u JSON dosyalarından kur.

Her persona için `src/llm/responses/<persona>.json` okunur, embedding üretilir,
ChromaDB persistent store'a yazılır. Festival'den önce bir kere çalıştır.

Kullanım:
    python scripts/build_rag_store.py             # tüm persona'lar
    python scripts/build_rag_store.py cezeri      # sadece cezeri
    python scripts/build_rag_store.py --reset     # önce sil, sonra yükle
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.core.config import get_config
from src.llm.rag_store import ChromaRAGStore

RESPONSES_DIR = Path("src/llm/responses")


async def build(personas: list[str], reset: bool) -> int:
    cfg = get_config()
    store = ChromaRAGStore({"store_path": cfg.llm.rag.store_path})

    files: list[Path]
    if personas:
        files = [RESPONSES_DIR / f"{p}.json" for p in personas]
    else:
        files = sorted(RESPONSES_DIR.glob("*.json"))

    if not files:
        print(f"HATA: {RESPONSES_DIR} altında JSON yok.")
        return 1

    total = 0
    for json_path in files:
        if not json_path.exists():
            print(f"ATLA: {json_path} yok.")
            continue
        persona_id = json_path.stem
        if reset:
            await store.reset(persona_id)
        count = await store.load_from_json(persona_id, json_path)
        print(f"[OK] {persona_id}: {count} cevap yüklendi.")
        total += count

    await store.close()
    print(f"\nToplam {total} cevap, store: {cfg.llm.rag.store_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BilimFest RAG store builder")
    parser.add_argument(
        "personas",
        nargs="*",
        help="Yüklenecek persona id'leri (boş = hepsi)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Mevcut koleksiyonu sil sonra yükle (idempotent değil)",
    )
    args = parser.parse_args()
    return asyncio.run(build(args.personas, args.reset))


if __name__ == "__main__":
    sys.exit(main())
