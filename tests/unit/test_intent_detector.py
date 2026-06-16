"""Keyword tabanlı intent detector testleri (M8 + TR normalize dahil)."""

from __future__ import annotations

import pytest

from src.core.interfaces import SessionState
from src.intent.detector import KeywordIntentDetector, turkish_lower


@pytest.fixture
def detector() -> KeywordIntentDetector:
    return KeywordIntentDetector()


async def test_detect_cezeri_selection(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("Cezeri ile konuşmak istiyorum", SessionState.LISTENING)
    assert result.type == "selection"
    assert result.target == "cezeri"
    assert result.confidence > 0.5


async def test_detect_strong_farewell(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("Hoşça kal artık.", SessionState.LISTENING)
    assert result.type == "farewell"


async def test_detect_question(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("Robot nedir?", SessionState.LISTENING)
    assert result.type == "question"


async def test_detect_unclear(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("uydu uydu", SessionState.LISTENING)
    assert result.type == "unclear"


async def test_detect_empty_text(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("   ", SessionState.LISTENING)
    assert result.type == "unclear"
    assert result.confidence == 0.0


# --- M8 regresyon: "teşekkürler" + soru oturumu kapatmamalı ---


async def test_thanks_with_question_is_not_farewell(detector: KeywordIntentDetector) -> None:
    """Çocuk 'Teşekkürler, peki nasıl çalışıyor?' derse oturum KAPANMAMALI."""
    result = await detector.detect(
        "Teşekkürler, peki nasıl çalışıyor?", SessionState.LISTENING
    )
    assert result.type != "farewell"
    assert result.type == "question"


async def test_standalone_thanks_is_weak_farewell(detector: KeywordIntentDetector) -> None:
    """Sadece 'Teşekkürler' tek başına → veda (zayıf güven)."""
    result = await detector.detect("Teşekkürler", SessionState.LISTENING)
    assert result.type == "farewell"
    assert result.confidence < 0.9  # zayıf


async def test_yeter_substring_does_not_false_match(detector: KeywordIntentDetector) -> None:
    """'yeterli mi' içinde 'yeter' kelime-sınırı yüzünden veda olmamalı."""
    result = await detector.detect("Bu enerji yeterli mi acaba?", SessionState.LISTENING)
    assert result.type != "farewell"


# --- TR normalize ---


def test_turkish_lower_dotted_i() -> None:
    assert turkish_lower("İSTANBUL") == "istanbul"
    assert turkish_lower("ILIK") == "ılık"
    assert turkish_lower("Cezerî İLE") == "cezerî ile"


async def test_uppercase_turkish_selection(detector: KeywordIntentDetector) -> None:
    """Baş harfi büyük TR girişte keyword kaçmamalı."""
    result = await detector.detect("CEZERİ anlat", SessionState.LISTENING)
    assert result.type == "selection"
    assert result.target == "cezeri"


async def test_question_mark_alone_is_question(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("Bu da çalışıyor mu?", SessionState.LISTENING)
    assert result.type == "question"
