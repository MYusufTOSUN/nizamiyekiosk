"""LLM + RAG canlı CLI testi.

Soru → RAG sorgu → eşleşme varsa hazır cevap, yoksa Llama'dan streaming.
Cezerî persona ile çalışır.

Kullanım:
    python scripts/test_llm.py                          # interaktif REPL
    python scripts/test_llm.py "Fil saati nedir?"       # tek soru
    python scripts/test_llm.py --no-rag "..."           # RAG'ı bypass et
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from src.core.config import get_config
from src.llm.llama_local import LlamaConfig, LlamaLocalLLM
from src.llm.persona import get_persona
from src.llm.rag_store import ChromaRAGStore


async def answer(
    llm: LlamaLocalLLM,
    rag: ChromaRAGStore | None,
    persona,
    question: str,
    rag_threshold: float,
) -> None:
    start = time.perf_counter()

    # 1) RAG dene
    if rag is not None:
        results = await rag.query(question, persona.id, top_k=3)
        if results and results[0].similarity >= rag_threshold:
            r = results[0]
            ms = int((time.perf_counter() - start) * 1000)
            print(f"[RAG hit, sim={r.similarity:.2f}, {ms}ms]")
            print(r.response_text)
            return
        if results:
            print(
                f"[RAG miss, en yakın sim={results[0].similarity:.2f} < {rag_threshold:.2f}]"
            )

    # 2) LLM streaming
    first_token_ms = None
    print("[LLM]: ", end="", flush=True)
    async for token in llm.generate_response(question, persona):
        if first_token_ms is None:
            first_token_ms = int((time.perf_counter() - start) * 1000)
        print(token, end="", flush=True)
    total_ms = int((time.perf_counter() - start) * 1000)
    print(f"\n[ttfb={first_token_ms}ms, total={total_ms}ms]")


async def main(question: str | None, no_rag: bool) -> int:
    cfg = get_config()
    persona = get_persona("cezeri")
    if persona is None:
        print("HATA: cezeri persona bulunamadı.")
        return 1

    print(f"Llama yükleniyor (model={cfg.llm.config.get('model_path', 'default')})...")
    llm = LlamaLocalLLM(LlamaConfig(**cfg.llm.config))
    await llm._ensure_model()

    rag = None
    if not no_rag:
        print(f"RAG store hazırlanıyor (path={cfg.llm.rag.store_path})...")
        rag = ChromaRAGStore({"store_path": cfg.llm.rag.store_path})
        await rag._ensure_ready()

    threshold = cfg.llm.rag.similarity_threshold

    if question:
        await answer(llm, rag, persona, question, threshold)
        return 0

    print("\nCezerî ile sohbet (boş satır = çık)\n")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        await answer(llm, rag, persona, q, threshold)
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BilimFest LLM canlı test")
    parser.add_argument("question", nargs="?", help="Tek soru. Boş = REPL.")
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="RAG'ı bypass et, doğrudan LLM'e sor.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.question, args.no_rag)))
