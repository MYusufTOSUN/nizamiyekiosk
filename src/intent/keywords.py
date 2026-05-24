"""Karakter seçimi ve farewell tespiti için keyword tabloları."""

from __future__ import annotations

# Karakter id → tetikleyici kelime kümeleri.
# Phase 1: sadece Cezerî. Diğer karakterler ileride eklenecek.
CHARACTER_KEYWORDS: dict[str, list[str]] = {
    "cezeri": [
        "cezeri",
        "cezerî",
        "el-cezeri",
        "el cezeri",
        "el-cezerî",
        "mucit",
        "robot ustası",
        "fil saati",
    ],
}

FAREWELL_KEYWORDS: list[str] = [
    "hoşça kal",
    "hoşçakal",
    "güle güle",
    "görüşürüz",
    "vedalaş",
    "bitti",
    "yeter",
    "kapat",
    "kapatalım",
    "teşekkür ederim",
    "teşekkürler",
]

QUESTION_HINTS: list[str] = [
    "?",
    "nedir",
    "nasıl",
    "niçin",
    "neden",
    "kim",
    "ne zaman",
    "nerede",
    "hangi",
    "kaç",
    "anlat",
    "söyle",
    "açıkla",
]
