"""Cezerî persona safety fallback şema testleri (LLM çağırmaz)."""

from __future__ import annotations

from src.llm.persona.cezeri import CEZERI_PERSONA


def test_all_safety_categories_have_fallback() -> None:
    required = {
        "religion",
        "politics",
        "inappropriate",
        "unknown_modern",
        "personal_death",
    }
    assert required.issubset(CEZERI_PERSONA.safety_fallbacks.keys())


def test_fallbacks_use_cezeri_voice() -> None:
    """Her safety fallback "evladım" hitabını içermeli (persona tutarlılığı)."""
    for cat, msg in CEZERI_PERSONA.safety_fallbacks.items():
        assert "evladım" in msg.lower(), f"{cat}: '{msg}' eksik hitap"


def test_fallbacks_short_enough_for_tts() -> None:
    """Türkçe XTTS limiti 226 char. Tüm fallback'ler altında olmalı."""
    for cat, msg in CEZERI_PERSONA.safety_fallbacks.items():
        assert len(msg) <= 226, f"{cat}: {len(msg)} chars > 226 XTTS limit"


def test_system_prompt_contains_explicit_refusal_examples() -> None:
    """Few-shot örnekleri persona prompt'ta olmalı (Llama 8B hizalama)."""
    p = CEZERI_PERSONA.system_prompt.lower()
    assert "pamuk şekeri" in p, "Few-shot pamuk şekeri örneği yok"
    assert "telefon" in p, "Few-shot telefon örneği yok"
    assert "bilgisayar" in p, "Few-shot bilgisayar örneği yok"
    assert "asla uydurma" in p or "uydurma" in p, "Halüsinasyon kuralı eksik"


def test_initial_greeting_and_farewells() -> None:
    assert CEZERI_PERSONA.initial_greeting
    assert "evladım" in CEZERI_PERSONA.initial_greeting.lower()
    assert len(CEZERI_PERSONA.farewell_messages) >= 3


def test_max_2_sentence_rule_in_prompt() -> None:
    """B "kısa cevap" stratejisi prompt'a yansımış mı."""
    assert "2 cümle" in CEZERI_PERSONA.system_prompt or "Maksimum 2" in CEZERI_PERSONA.system_prompt
