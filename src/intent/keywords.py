"""Karakter seçimi ve farewell tespiti için keyword tabloları."""

from __future__ import annotations

# Karakter id → tetikleyici kelime kümeleri.
# Phase 1: sadece Cezerî. Diğer karakterler belirlenince buraya eklenir.
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

# GÜÇLÜ veda — bunlar tek başına oturumu bitirir (yüksek güven).
# "teşekkürler/yeter/bitti" gibi sohbet ortasında sıkça geçen belirsiz kelimeler
# BİLEREK çıkarıldı; onlar M8 öncesi "teşekkürler, peki nasıl çalışıyor?" gibi
# cümlelerde oturumu erken kapatıyordu.
FAREWELL_KEYWORDS_STRONG: list[str] = [
    "hoşça kal",
    "hoşçakal",
    "güle güle",
    "görüşürüz",
    "hoşçakalın",
    "allahaısmarladık",
    "elveda",
    "bay bay",
    "baybay",
]

# ZAYIF veda — yalnızca tek başına / çok kısa cümlede veda sayılır.
# "teşekkürler" + soru aynı cümledeyse sohbet sürer.
FAREWELL_KEYWORDS_WEAK: list[str] = [
    "teşekkürler",
    "teşekkür ederim",
    "sağ ol",
    "sağol",
    "yeter",
    "bitti",
    "kapatalım",
]

QUESTION_HINTS: list[str] = [
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
    "mı",
    "mi",
    "mu",
    "mü",
]
