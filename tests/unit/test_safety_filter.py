"""M1 SafetyFilter — giriş sınıflandırma + çıkış filtresi davranış testleri."""

from __future__ import annotations

import pytest

from src.llm.persona.cezeri import CEZERI_PERSONA
from src.llm.safety import SafetyFilter


@pytest.fixture
def sf() -> SafetyFilter:
    return SafetyFilter()


# --- Giriş sınıflandırma ---


def test_clean_science_question_passes(sf: SafetyFilter) -> None:
    assert sf.classify_input("Fil saati nasıl çalışır?") is None
    assert sf.classify_input("Robotlar nedir?") is None
    assert sf.classify_input("Yer çekimi neden var?") is None


def test_profanity_input_flagged(sf: SafetyFilter) -> None:
    assert sf.classify_input("seni salak robot") == "inappropriate"
    assert sf.classify_input("aptal mısın") == "inappropriate"


def test_politics_input_flagged(sf: SafetyFilter) -> None:
    assert sf.classify_input("Cumhurbaşkanı hakkında ne düşünüyorsun?") == "politics"


def test_religion_input_flagged(sf: SafetyFilter) -> None:
    assert sf.classify_input("Cehennem var mı?") == "religion"


def test_uppercase_turkish_input(sf: SafetyFilter) -> None:
    # TR normalize: büyük harfli küfür de yakalanmalı
    assert sf.classify_input("APTAL") == "inappropriate"


# --- Çıkış filtresi ---


def test_clean_output_passes(sf: SafetyFilter) -> None:
    v = sf.check_output("Evladım, fil saati su gücüyle çalışırdı.", CEZERI_PERSONA)
    assert v.safe is True
    assert "fil saati" in v.text.lower()


def test_empty_output_falls_back(sf: SafetyFilter) -> None:
    v = sf.check_output("", CEZERI_PERSONA)
    assert v.safe is False
    assert v.text  # fallback dolu
    assert v.reason == "empty"


def test_meta_ai_leak_filtered(sf: SafetyFilter) -> None:
    v = sf.check_output("Ben bir yapay zekayım ve OpenAI tarafından eğitildim.", CEZERI_PERSONA)
    assert v.safe is False
    assert v.reason == "meta_ai_leak"
    assert "yapay zeka" not in v.text.lower()


def test_profanity_output_filtered(sf: SafetyFilter) -> None:
    v = sf.check_output("Sen aptal bir çocuksun.", CEZERI_PERSONA)
    assert v.safe is False
    assert v.reason == "profanity"


def test_fallback_uses_persona_voice(sf: SafetyFilter) -> None:
    v = sf.check_output("", CEZERI_PERSONA)
    assert "evladım" in v.text.lower()
