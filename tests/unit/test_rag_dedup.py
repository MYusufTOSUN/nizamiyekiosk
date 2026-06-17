"""RAG per-example dedup + margin-gate mantığı (embedding modeli yüklemez)."""

from __future__ import annotations

from src.llm.rag_store import ChromaRAGStore


def _raw(entries: list[tuple[str, float]]) -> dict:
    """entries = [(entry_id, distance), ...] → Chroma query raw formatı."""
    metas = [{"entry_id": eid, "response": f"cevap-{eid}", "topic": eid} for eid, _ in entries]
    dists = [d for _, d in entries]
    return {"metadatas": [metas], "distances": [dists]}


def test_dedup_keeps_max_similarity_per_entry() -> None:
    store = ChromaRAGStore({"store_path": "x"})
    # Aynı entry'nin iki örneği: dist 0.2 (sim 0.8) ve dist 0.05 (sim 0.95)
    raw = _raw([("cezeri_001", 0.20), ("cezeri_001", 0.05), ("cezeri_002", 0.30)])
    results = store._dedup_by_entry(raw)
    # cezeri_001 tek kez, en yüksek benzerlikle (0.95)
    ids = [r.metadata["entry_id"] for r in results]
    assert ids.count("cezeri_001") == 1
    top = next(r for r in results if r.metadata["entry_id"] == "cezeri_001")
    assert abs(top.similarity - 0.95) < 1e-6


def test_dedup_sorted_desc() -> None:
    store = ChromaRAGStore({"store_path": "x"})
    raw = _raw([("a", 0.4), ("b", 0.1), ("c", 0.25)])
    results = store._dedup_by_entry(raw)
    sims = [r.similarity for r in results]
    assert sims == sorted(sims, reverse=True)
    assert results[0].metadata["entry_id"] == "b"  # en düşük mesafe = en yüksek benzerlik


def test_dedup_empty() -> None:
    store = ChromaRAGStore({"store_path": "x"})
    assert store._dedup_by_entry({"metadatas": [[]], "distances": [[]]}) == []


# --- Margin-gate karar mantığı (pipeline ile aynı formül) ---


def _gate(top1: float, top2: float | None, threshold: float, margin: float) -> bool:
    if top1 < threshold:
        return False
    if top2 is None:
        return True
    return (top1 - top2) >= margin


def test_margin_gate_accepts_confident() -> None:
    assert _gate(0.90, 0.80, 0.84, 0.02) is True


def test_margin_gate_rejects_ambiguous() -> None:
    # top1 eşik üstü ama top2 çok yakın → reddet (LLM'e düş)
    assert _gate(0.88, 0.875, 0.84, 0.02) is False


def test_margin_gate_rejects_below_threshold() -> None:
    assert _gate(0.83, 0.10, 0.84, 0.02) is False


def test_margin_gate_single_candidate_accepts() -> None:
    assert _gate(0.85, None, 0.84, 0.02) is True
