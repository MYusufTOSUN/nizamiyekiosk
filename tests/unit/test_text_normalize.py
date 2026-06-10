"""TR text normalize testleri."""

from __future__ import annotations

import pytest

from src.tts.text_normalize import normalize_for_tts, number_to_tr


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "sıfır"),
        (1, "bir"),
        (10, "on"),
        (12, "on iki"),
        (25, "yirmi beş"),
        (99, "doksan dokuz"),
        (100, "yüz"),
        (101, "yüz bir"),
        (200, "iki yüz"),
        (350, "üç yüz elli"),
        (1206, "bin iki yüz altı"),
        (2026, "iki bin yirmi altı"),
    ],
)
def test_number_to_tr(n: int, expected: str) -> None:
    assert number_to_tr(n) == expected


def test_normalize_numbers_in_text() -> None:
    text = "12. yüzyılda 1206 yılında doğdum"
    result = normalize_for_tts(text)
    assert "on iki" in result
    assert "bin iki yüz altı" in result
    assert "12" not in result
    assert "1206" not in result


def test_normalize_abbreviations() -> None:
    text = "Cezerî m.s. 1136 vb. ustalar"
    result = normalize_for_tts(text)
    assert "milattan sonra" in result
    assert "ve benzeri" in result
    assert "m.s." not in result


def test_normalize_collapse_whitespace() -> None:
    assert normalize_for_tts("  hello   world  ") == "hello world"


def test_normalize_preserves_real_text() -> None:
    text = "Fil saati benim en güzel eserim"
    result = normalize_for_tts(text)
    assert "fil saati" in result.lower()
    assert "eserim" in result


def test_normalize_large_number_kept_as_is() -> None:
    """10000+ rakam çevrilmiyor, sözel okunması zor."""
    text = "12345 yıl önce"
    result = normalize_for_tts(text)
    assert "12345" in result
