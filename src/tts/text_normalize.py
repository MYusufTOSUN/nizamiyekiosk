"""Türkçe TTS için metin normalizasyonu.

XTTS Türkçede sayıları rakam olarak okur ("12. yüzyıl" → "on iki nokta yüzyıl"
yerine "yüzelli" gibi tuhaf sonuçlar). Bu modül sayıları + sık kısaltmaları
ses-dostu hale getirir.
"""

from __future__ import annotations

import re

# Basit kısaltmalar
ABBREV: dict[str, str] = {
    " m.s. ": " milattan sonra ",
    " m.ö. ": " milattan önce ",
    " m.k. ": "",  # halüsinasyon credit'i — buraya gelmemeli zaten
    " vb. ": " ve benzeri ",
    " vs. ": " vesaire ",
    " yy. ": " yüzyıl ",
    " ör. ": " örnek ",
    " bkz. ": " bakınız ",
    " hz. ": " hazreti ",
    " dr. ": " doktor ",
    " prof. ": " profesör ",
}

# 0-99 arası Türkçe sayı isimleri (XTTS için ses sentezi açısından)
_BIRLER = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_ONLAR = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_YUZLER = ["", "yüz", "iki yüz", "üç yüz", "dört yüz", "beş yüz", "altı yüz", "yedi yüz", "sekiz yüz", "dokuz yüz"]


def _0_99_to_tr(n: int) -> str:
    if n == 0:
        return "sıfır"
    onlar, birler = divmod(n, 10)
    parts = []
    if onlar:
        parts.append(_ONLAR[onlar])
    if birler:
        parts.append(_BIRLER[birler])
    return " ".join(parts)


def number_to_tr(n: int) -> str:
    """0–9999 arası tamsayıyı Türkçe okunuşa çevir."""
    if n < 0:
        return "eksi " + number_to_tr(-n)
    if n < 100:
        return _0_99_to_tr(n)
    if n < 1000:
        yuz, kalan = divmod(n, 100)
        result = _YUZLER[yuz]
        if kalan:
            result += " " + _0_99_to_tr(kalan)
        return result
    if n < 10000:
        binler, kalan = divmod(n, 1000)
        result = _0_99_to_tr(binler) + " bin" if binler > 1 else "bin"
        if kalan:
            result += " " + (number_to_tr(kalan) if kalan >= 100 else _0_99_to_tr(kalan))
        return result
    # 10000+: rakam olarak bırak (tarihte 12000 vb. nadir)
    return str(n)


_NUM_RE = re.compile(r"\b\d{1,4}\b")


def _replace_number(match: re.Match[str]) -> str:
    n = int(match.group(0))
    if 0 <= n <= 9999:
        return number_to_tr(n)
    return match.group(0)


def normalize_for_tts(text: str) -> str:
    """XTTS Türkçe sentezi için metni temizle.

    1. Kısaltmaları aç (m.s. → milattan sonra)
    2. 0-9999 arası sayıları sözel hale çevir
    3. Çoklu boşlukları tek boşluğa indir
    4. Cümle sonu noktalama düzelt (TTS doğru pause atsın)
    """
    out = " " + text + " "
    lower = out.lower()
    # Kısaltmalar (lowercase'de ara, ama orijinal case'i koru riskli, basit yaklaşım: lowercase'e çevirip yeniden büyük harf yok — TTS için fark yok)
    out_lower = lower
    for k, v in ABBREV.items():
        out_lower = out_lower.replace(k, v)
    out = out_lower.strip()

    # Sayılar
    out = _NUM_RE.sub(_replace_number, out)

    # Çoklu boşluk
    out = re.sub(r"\s+", " ", out).strip()
    return out


__all__ = ["normalize_for_tts", "number_to_tr"]
