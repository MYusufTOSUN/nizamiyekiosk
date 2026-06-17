"""RAG precision/recall değerlendirme + threshold/margin tuning.

Etiketli probe setini (tests/rag_eval/<persona>_probes.json) RAG store'a karşı
çalıştırır. Her probe'u BİR kez sorgulayıp top-N benzerlikleri toplar, sonra
(threshold, margin) kombinasyonlarını Python'da süpürerek en iyi ayarı bulur.

Metrikler:
  - correct_hit:  should-hit probe, doğru entry döndü
  - wrong_hit:    should-hit AMA yanlış entry, VEYA should-miss AMA hit verdi  ← ASIL SORUN
  - miss:         should-miss, doğru şekilde LLM'e düştü / should-hit ama düştü

Kullanım:
    python scripts/eval_rag.py                 # cezeri, embedding-only
    python scripts/eval_rag.py --reranker      # cross-encoder ile
    python scripts/eval_rag.py --threshold 0.8 --margin 0.04   # tek ayar detayı
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.core.config import get_config
from src.llm.rag_store import ChromaRAGStore

PROBE_DIR = Path("tests/rag_eval")


async def _collect(store: ChromaRAGStore, persona: str, probes: list[dict]) -> list[dict]:
    """Her probe için top-5 (entry_id, similarity) topla."""
    out = []
    for p in probes:
        results = await store.query(p["q"], persona, top_k=5)
        ranked = [
            {"id": str(r.metadata.get("entry_id", "")), "sim": r.similarity}
            for r in results
        ]
        out.append({**p, "ranked": ranked})
    return out


def _classify(probe: dict, threshold: float, margin: float) -> str:
    """Tek probe + (threshold, margin) → 'correct' | 'wrong_hit' | 'false_miss' | 'correct_miss'."""
    ranked = probe["ranked"]
    top1 = ranked[0] if ranked else None
    top2 = ranked[1] if len(ranked) > 1 else None

    # Gate: hit mi?
    is_hit = (
        top1 is not None
        and top1["sim"] >= threshold
        and (top2 is None or (top1["sim"] - top2["sim"]) >= margin)
    )

    if probe["expect"] == "miss":
        return "correct_miss" if not is_hit else "wrong_hit"
    # expect == "hit"
    if not is_hit:
        return "false_miss"
    return "correct" if top1["id"] == probe["id"] else "wrong_hit"


def _score(collected: list[dict], threshold: float, margin: float) -> dict:
    tally = {"correct": 0, "wrong_hit": 0, "false_miss": 0, "correct_miss": 0}
    for p in collected:
        tally[_classify(p, threshold, margin)] += 1
    # Hedef: wrong_hit (alakasız cevap) AĞIR cezalı; correct + correct_miss ödüllü
    composite = tally["correct"] + tally["correct_miss"] - 5 * tally["wrong_hit"] - tally["false_miss"]
    tally["score"] = composite
    return tally


async def main(persona: str, reranker: bool, fixed_t: float | None, fixed_m: float | None) -> int:
    cfg = get_config()
    store = ChromaRAGStore(
        {
            "store_path": cfg.llm.rag.store_path,
            "candidate_pool": cfg.llm.rag.candidate_pool,
            "use_reranker": reranker,
            "reranker_model": cfg.llm.rag.reranker_model,
        }
    )

    probe_file = PROBE_DIR / f"{persona}_probes.json"
    probes = json.loads(probe_file.read_text(encoding="utf-8"))["probes"]
    n_hit = sum(1 for p in probes if p["expect"] == "hit")
    n_miss = sum(1 for p in probes if p["expect"] == "miss")
    print(f"Probe: {len(probes)} ({n_hit} hit-beklenen, {n_miss} miss-beklenen), reranker={reranker}")
    print("Sorgulanıyor...")
    collected = await _collect(store, persona, probes)

    if fixed_t is not None:
        t, m = fixed_t, fixed_m or 0.0
        tally = _score(collected, t, m)
        print(f"\n=== threshold={t} margin={m} ===")
        _print_tally(tally, len(probes))
        print("\n--- Probe detay ---")
        for p in collected:
            cls = _classify(p, t, m)
            top = p["ranked"][0] if p["ranked"] else {"id": "-", "sim": 0}
            flag = "X" if cls in ("wrong_hit", "false_miss") else "OK"
            print(f"  {flag:3}[{cls:12}] sim={top['sim']:.3f} -> {top['id']:12} | {p['q'][:45]}")
        await store.close()
        return 0

    # Sweep
    print("\n=== Threshold × Margin sweep ===")
    best = None
    rows = []
    # 0.30..0.94 — embedding rejimi ~0.84, reranker (sigmoid) rejimi daha düşük olabilir
    for t in [round(0.30 + 0.02 * i, 2) for i in range(33)]:
        for m in [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]:
            tally = _score(collected, t, m)
            rows.append((t, m, tally))
            if best is None or tally["score"] > best[2]["score"]:
                best = (t, m, tally)

    # En iyi 8 kombinasyonu göster
    rows.sort(key=lambda r: r[2]["score"], reverse=True)
    print(f"{'thr':>5} {'margin':>6} {'correct':>7} {'wrong':>6} {'fmiss':>6} {'cmiss':>6} {'score':>6}")
    for t, m, tally in rows[:8]:
        print(
            f"{t:>5} {m:>6} {tally['correct']:>7} {tally['wrong_hit']:>6} "
            f"{tally['false_miss']:>6} {tally['correct_miss']:>6} {tally['score']:>6}"
        )

    assert best is not None
    bt, bm, btally = best
    print(f"\n>>> EN İYİ: threshold={bt}, margin={bm}")
    _print_tally(btally, len(probes))
    print(f"\nconfig.yaml'a uygula:  similarity_threshold: {bt}  margin: {bm}")
    await store.close()
    return 0


def _print_tally(tally: dict, total: int) -> None:
    print(
        f"  correct={tally['correct']}  wrong_hit={tally['wrong_hit']}  "
        f"false_miss={tally['false_miss']}  correct_miss={tally['correct_miss']}  "
        f"(toplam {total})"
    )
    if tally["wrong_hit"] == 0:
        print("  >> ALAKASIZ CEVAP YOK [OK]")
    else:
        print(f"  >> {tally['wrong_hit']} alakasiz/yanlis hit var [X]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RAG precision eval + tuning")
    ap.add_argument("--persona", default="cezeri")
    ap.add_argument("--reranker", action="store_true")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--margin", type=float, default=None)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.persona, args.reranker, args.threshold, args.margin)))
