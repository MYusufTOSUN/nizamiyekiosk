"""B4 — XTTS disk audio cache + B5 speaker latent cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tts.xtts_local import XTTSLocalTTS, _cache_key


def test_cache_key_deterministic() -> None:
    k1 = _cache_key("merhaba", "cezeri", 1.0, "tr")
    k2 = _cache_key("merhaba", "cezeri", 1.0, "tr")
    assert k1 == k2
    assert len(k1) == 24


def test_cache_key_changes_with_text() -> None:
    k1 = _cache_key("a", "cezeri", 1.0, "tr")
    k2 = _cache_key("b", "cezeri", 1.0, "tr")
    assert k1 != k2


def test_cache_key_changes_with_voice() -> None:
    k1 = _cache_key("a", "cezeri", 1.0, "tr")
    k2 = _cache_key("a", "einstein", 1.0, "tr")
    assert k1 != k2


def test_cache_key_changes_with_speed() -> None:
    k1 = _cache_key("a", "cezeri", 1.0, "tr")
    k2 = _cache_key("a", "cezeri", 1.2, "tr")
    assert k1 != k2


def test_cache_path_returns_none_when_disabled(tmp_path: Path) -> None:
    tts = XTTSLocalTTS({"cache_enabled": False, "cache_dir": str(tmp_path / "tc")})
    assert tts._cache_path("hi", "cezeri", 1.0) is None


def test_cache_path_creates_dir(tmp_path: Path) -> None:
    cd = tmp_path / "tc"
    tts = XTTSLocalTTS({"cache_dir": str(cd)})
    p = tts._cache_path("hi", "cezeri", 1.0)
    assert p is not None
    assert cd.exists()
    assert p.parent == cd
    assert p.suffix == ".pcm"


def test_cache_prune_keeps_only_max(tmp_path: Path) -> None:
    cd = tmp_path / "tc"
    cd.mkdir()
    # 5 fake cache entry oluştur, max=3'e prune
    for i in range(5):
        f = cd / f"{i:02d}.pcm"
        f.write_bytes(b"x")
    tts = XTTSLocalTTS({"cache_dir": str(cd), "cache_max_entries": 3})
    tts._prune_cache()
    remaining = sorted(cd.glob("*.pcm"))
    assert len(remaining) == 3
    # En yeni 3 dosya kalır (mtime-based)
    assert [f.name for f in remaining] == ["02.pcm", "03.pcm", "04.pcm"]
