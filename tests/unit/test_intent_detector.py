"""Keyword tabanlı intent detector testleri."""

from __future__ import annotations

import pytest

from src.core.interfaces import SessionState
from src.intent.detector import KeywordIntentDetector


@pytest.fixture
def detector() -> KeywordIntentDetector:
    return KeywordIntentDetector()


async def test_detect_cezeri_selection(detector: KeywordIntentDetector) -> None:
    result = await detector.detect("Cezeri ile konuşmak istiyorum", SessionState.LISTENING)
    assert result.type == "selection"
    assert result.target == "cezeri"
    assert result.confidence > 0.5


async def test_detect_farewell(detector: KeywordIntentDetector) -> None:
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
