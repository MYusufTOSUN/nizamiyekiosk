"""Cezerî RAG seed JSON şema testleri (embedding/vector store kurmaz)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CEZERI_JSON = Path("src/llm/responses/cezeri.json")


@pytest.fixture(scope="module")
def cezeri_data() -> dict:
    return json.loads(CEZERI_JSON.read_text(encoding="utf-8"))


def test_top_level_shape(cezeri_data: dict) -> None:
    assert cezeri_data["persona_id"] == "cezeri"
    assert isinstance(cezeri_data["responses"], list)
    # 200 -> 199 (cezeri_011/154 duplicate birleşti) -> 205 (RAG finetune: hologram
    # kimlik/algı boşlukları için 6 yeni entry — sen gerçek misin, robot musun,
    # beni görüyor musun, nasıl konuşuyorsun, kaç icat, sevgi).
    assert len(cezeri_data["responses"]) >= 200


def test_every_entry_has_required_fields(cezeri_data: dict) -> None:
    required = {"id", "intent_keywords", "question_examples", "response", "topic"}
    for entry in cezeri_data["responses"]:
        missing = required - entry.keys()
        assert not missing, f"{entry.get('id')} eksik alanlar: {missing}"
        assert isinstance(entry["question_examples"], list)
        # Eval sonrası her entry 5-6 örneğe çıkarıldı (recall + ayrışma).
        assert len(entry["question_examples"]) >= 3
        assert isinstance(entry["intent_keywords"], list)
        assert len(entry["response"]) > 20  # kısa anlamsız metinleri ele


def test_examples_rich_for_recall(cezeri_data: dict) -> None:
    """Recall için entry başına ortalama >= 4 soru örneği olmalı (eval bulgusu)."""
    counts = [len(r["question_examples"]) for r in cezeri_data["responses"]]
    avg = sum(counts) / len(counts)
    assert avg >= 4.5, f"Ortalama örnek {avg:.1f} < 4.5 (recall düşer)"


def test_ids_unique(cezeri_data: dict) -> None:
    ids = [r["id"] for r in cezeri_data["responses"]]
    assert len(ids) == len(set(ids)), "Tekrar eden id var"


def test_response_uses_persona_voice(cezeri_data: dict) -> None:
    # Cezerî kimliği: "evladım" hitabı çoğu cevapta geçmeli (en az %75 — 200 entry için)
    evladım_count = sum(1 for r in cezeri_data["responses"] if "evladım" in r["response"].lower())
    assert evladım_count / len(cezeri_data["responses"]) >= 0.75


def test_all_responses_under_xtts_limit(cezeri_data: dict) -> None:
    """Türkçe XTTS limiti 226 char. Aşan response splitter çağırır; ama 250'yi
    çok aşan response zayıf yazılmış demektir — kalite kontrolü."""
    long_ones = [r for r in cezeri_data["responses"] if len(r["response"]) > 290]
    assert not long_ones, f"Response > 290 char: {[r['id'] for r in long_ones]}"


def test_each_response_ends_with_question(cezeri_data: dict) -> None:
    """Persona kuralı: cevap soru ile bitsin (ziyaretçiyi konuşmaya teşvik et).
    Hedef %85+ — kısa fallback tipi cevaplarda istisna olabilir."""
    with_question = sum(
        1 for r in cezeri_data["responses"] if r["response"].rstrip().endswith("?")
    )
    ratio = with_question / len(cezeri_data["responses"])
    assert ratio >= 0.80, f"Soruyla biten: {ratio:.0%} (hedef ≥80%)"


def test_topic_distribution_diverse(cezeri_data: dict) -> None:
    """Tek bir topic 200'ün %20'sinden fazlasını oluşturmamalı — çeşitlilik."""
    from collections import Counter

    topics = Counter(r.get("topic", "") for r in cezeri_data["responses"])
    most_common_topic, most_common_count = topics.most_common(1)[0]
    assert most_common_count <= len(cezeri_data["responses"]) * 0.2, (
        f"Topic '{most_common_topic}' over-represented: {most_common_count}"
    )
