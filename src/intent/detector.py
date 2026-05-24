"""Keyword tabanlı niyet tespiti.

Phase 1 implementasyonu: küçük harfe çevir, kelime kümelerini ara. Phase 3+
ile LLM-classifier fallback eklenebilir.
"""

from __future__ import annotations

from src.core.interfaces import Intent, IntentDetector, SessionState
from src.intent.keywords import CHARACTER_KEYWORDS, FAREWELL_KEYWORDS, QUESTION_HINTS


def _normalize(text: str) -> str:
    return text.strip().lower()


class KeywordIntentDetector(IntentDetector):
    """Basit, deterministik niyet tespit edici."""

    async def detect(self, text: str, current_state: SessionState) -> Intent:
        normalized = _normalize(text)

        if not normalized:
            return Intent(type="unclear", confidence=0.0, raw_text=text)

        # 1) Farewell (her state'te öncelikli)
        for kw in FAREWELL_KEYWORDS:
            if kw in normalized:
                return Intent(type="farewell", confidence=0.9, raw_text=text)

        # 2) Karakter seçimi
        for character_id, keywords in CHARACTER_KEYWORDS.items():
            for kw in keywords:
                if kw in normalized:
                    return Intent(
                        type="selection",
                        target=character_id,
                        confidence=0.85,
                        raw_text=text,
                    )

        # 3) Soru
        for hint in QUESTION_HINTS:
            if hint in normalized:
                return Intent(type="question", confidence=0.6, raw_text=text)

        return Intent(type="unclear", confidence=0.2, raw_text=text)
