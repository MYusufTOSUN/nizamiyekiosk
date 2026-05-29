"""XTTSConfig + voice reference scan testleri (model yüklemez)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tts.xtts_local import XTTSConfig, XTTSLocalTTS


def test_xtts_config_defaults() -> None:
    cfg = XTTSConfig()
    assert cfg.model_name == "tts_models/multilingual/multi-dataset/xtts_v2"
    assert cfg.language == "tr"
    assert cfg.output_chunk_samples == 480
    assert cfg.device == "cuda"


def test_xtts_config_dict_override() -> None:
    tts = XTTSLocalTTS({"language": "en", "device": "cpu"})
    assert tts.config.language == "en"
    assert tts.config.device == "cpu"


def test_voice_refs_empty_when_dir_missing(tmp_path: Path) -> None:
    tts = XTTSLocalTTS({"voices_dir": str(tmp_path / "nonexistent")})
    assert tts._scan_voice_refs() == {}


def test_voice_refs_picks_only_ref_wavs(tmp_path: Path) -> None:
    cezeri_dir = tmp_path / "cezeri"
    cezeri_dir.mkdir(parents=True)
    (cezeri_dir / "ref_01.wav").write_bytes(b"")
    (cezeri_dir / "ref_02.wav").write_bytes(b"")
    (cezeri_dir / "random.txt").write_text("ignore me")
    (cezeri_dir / "other.wav").write_bytes(b"")  # ref_ prefix yok, atlanmalı

    other_dir = tmp_path / "stranger"
    other_dir.mkdir()
    (other_dir / "ref_01.wav").write_bytes(b"")

    tts = XTTSLocalTTS({"voices_dir": str(tmp_path)})
    refs = tts._scan_voice_refs()
    assert sorted(refs.keys()) == ["cezeri", "stranger"]
    assert len(refs["cezeri"]) == 2
    assert all("ref_" in Path(p).name for p in refs["cezeri"])


def test_voice_refs_sorted(tmp_path: Path) -> None:
    d = tmp_path / "x"
    d.mkdir()
    (d / "ref_03.wav").write_bytes(b"")
    (d / "ref_01.wav").write_bytes(b"")
    (d / "ref_02.wav").write_bytes(b"")
    tts = XTTSLocalTTS({"voices_dir": str(tmp_path)})
    refs = tts._scan_voice_refs()
    names = [Path(p).name for p in refs["x"]]
    assert names == ["ref_01.wav", "ref_02.wav", "ref_03.wav"]
