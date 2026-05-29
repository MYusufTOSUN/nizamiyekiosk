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
    assert len(cezeri_data["responses"]) >= 15


def test_every_entry_has_required_fields(cezeri_data: dict) -> None:
    required = {"id", "intent_keywords", "question_examples", "response", "topic"}
    for entry in cezeri_data["responses"]:
        missing = required - entry.keys()
        assert not missing, f"{entry.get('id')} eksik alanlar: {missing}"
        assert isinstance(entry["question_examples"], list)
        assert len(entry["question_examples"]) >= 1
        assert isinstance(entry["intent_keywords"], list)
        assert len(entry["response"]) > 20  # kısa anlamsız metinleri ele


def test_ids_unique(cezeri_data: dict) -> None:
    ids = [r["id"] for r in cezeri_data["responses"]]
    assert len(ids) == len(set(ids)), "Tekrar eden id var"


def test_response_uses_persona_voice(cezeri_data: dict) -> None:
    # Cezerî kimliği: "evladım" hitabı çoğu cevapta geçmeli (en az %60)
    evladım_count = sum(1 for r in cezeri_data["responses"] if "evladım" in r["response"].lower())
    assert evladım_count / len(cezeri_data["responses"]) >= 0.6
