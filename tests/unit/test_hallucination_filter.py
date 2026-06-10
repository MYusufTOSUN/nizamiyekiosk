"""A4 — Whisper halüsinasyon blocklist case/whitespace/punctuation normalize."""

from __future__ import annotations

import pytest

from src.stt.whisper_local import _is_hallucination, _normalize_for_blocklist


@pytest.mark.parametrize(
    "raw",
    [
        "Altyazı M.K.",
        "ALTYAZI M.K.",
        "altyazı m.k.",
        " Altyazı M.K. ",
        "Altyazı M.K.!",
        "altyazi M.K.",
        '"Altyazı M.K."',
        "Abone ol",
        "ABONE OL",
        "abone ol.",
    ],
)
def test_blocklist_catches_variants(raw: str) -> None:
    assert _is_hallucination(raw), f"Blocklist missed: {raw!r}"


def test_real_speech_not_filtered() -> None:
    assert not _is_hallucination("Fil saati nedir?")
    assert not _is_hallucination("Robotlar nasıl çalışır?")
    assert not _is_hallucination("Cezerî bana atölyeni anlat.")


def test_normalize_collapses_whitespace() -> None:
    assert _normalize_for_blocklist("  Hello  world  ") == "hello world"
    assert _normalize_for_blocklist("Hello\tworld\nfoo") == "hello world foo"


def test_normalize_strips_punctuation() -> None:
    assert _normalize_for_blocklist("Hello.") == "hello"
    assert _normalize_for_blocklist("!!Hello!!") == "hello"
    assert _normalize_for_blocklist('"Hello"') == "hello"
