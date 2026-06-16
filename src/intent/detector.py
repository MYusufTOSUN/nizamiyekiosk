"""Keyword tabanlı niyet tespiti.

Türkçe-bilinçli normalizasyon + kelime-sınırı eşleşmesi. Phase 3+ LLM-classifier
fallback eklenebilir.
"""

from __future__ import annotations

import re

from src.core.interfaces import Intent, IntentDetector, SessionState
from src.intent.keywords import (
    CHARACTER_KEYWORDS,
    FAREWELL_KEYWORDS_STRONG,
    FAREWELL_KEYWORDS_WEAK,
    QUESTION_HINTS,
)

# Türkçe küçük harf: "İ"→"i", "I"→"ı" (Python str.lower() bunu yanlış yapar).
_TR_LOWER_MAP = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})


def turkish_lower(text: str) -> str:
    """Türkçe-doğru küçük harf dönüşümü."""
    return text.translate(_TR_LOWER_MAP).lower()


def _normalize(text: str) -> str:
    return turkish_lower(text.strip())


def _contains_word(haystack: str, needle: str) -> bool:
    """Kelime-sınırı eşleşmesi. Çok kelimeli needle'lar için substring + sınır.

    "yeter" → "yeterli" eşleşmez; "hoşça kal" → cümle içinde tam ifade aranır.
    """
    if " " in needle:
        # Çok kelimeli ifade: kelime sınırlarıyla sarılmış substring
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    else:
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    return re.search(pattern, haystack) is not None


# "Çok kısa cümle" = zayıf vedanın oturumu bitirmesine izin verdiğimiz sınır.
_WEAK_FAREWELL_MAX_WORDS = 3


class KeywordIntentDetector(IntentDetector):
    """Türkçe-bilinçli, deterministik niyet tespit edici."""

    async def detect(self, text: str, current_state: SessionState) -> Intent:
        normalized = _normalize(text)
        if not normalized:
            return Intent(type="unclear", confidence=0.0, raw_text=text)

        word_count = len(normalized.split())
        has_question_mark = "?" in text

        # 1) GÜÇLÜ veda — her durumda oturumu bitir
        for kw in FAREWELL_KEYWORDS_STRONG:
            if _contains_word(normalized, kw):
                return Intent(type="farewell", confidence=0.92, raw_text=text)

        # 2) Karakter seçimi (vedadan önce kontrol — "cezeri görüşürüz" gibi nadir
        #    çakışmada seçim öncelikli değil; ama selection genelde net)
        for character_id, keywords in CHARACTER_KEYWORDS.items():
            for kw in keywords:
                if _contains_word(normalized, kw):
                    return Intent(
                        type="selection",
                        target=character_id,
                        confidence=0.85,
                        raw_text=text,
                    )

        # 3) ZAYIF veda — yalnızca çok kısa cümlede VE soru işareti yoksa
        #    "teşekkürler" tek başına → veda; "teşekkürler peki nasıl?" → soru
        if word_count <= _WEAK_FAREWELL_MAX_WORDS and not has_question_mark:
            for kw in FAREWELL_KEYWORDS_WEAK:
                if _contains_word(normalized, kw):
                    return Intent(type="farewell", confidence=0.7, raw_text=text)

        # 4) Soru
        if has_question_mark:
            return Intent(type="question", confidence=0.75, raw_text=text)
        for hint in QUESTION_HINTS:
            if _contains_word(normalized, hint):
                return Intent(type="question", confidence=0.6, raw_text=text)

        return Intent(type="unclear", confidence=0.2, raw_text=text)


__all__ = ["KeywordIntentDetector", "turkish_lower"]
