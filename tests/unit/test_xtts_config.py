"""XTTSConfig + voice reference scan testleri (model yüklemez)."""

from __future__ import annotations

from pathlib import Path

from src.tts.xtts_local import (
    MAX_CHARS_PER_LANGUAGE,
    XTTSConfig,
    XTTSLocalTTS,
    split_text_for_xtts,
)


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


def test_split_short_text_returns_as_is() -> None:
    text = "Aleyküm selam evladım."
    assert split_text_for_xtts(text, "tr") == [text]


def test_split_empty_text() -> None:
    assert split_text_for_xtts("", "tr") == []
    assert split_text_for_xtts("   ", "tr") == []


def test_split_long_text_at_sentence_boundaries() -> None:
    # Cezerî fil saati cevabı (304 char) — Türkçe limit 226
    text = (
        "Bismillah evladım, fil saati benim en güzel eserim. "
        "Bir fil sırtında saat kulesi, içinde su tankı, dakikalar geçtikçe "
        "küçük insanlar zil çalar, bir kuş öter. Su düzeyi belli bir noktaya "
        "gelince mekanizma tetiklenir, hareket başlar. Modeli müzede sergileniyor. "
        "Görmek ister misin?"
    )
    pieces = split_text_for_xtts(text, "tr")
    assert len(pieces) >= 2
    for p in pieces:
        assert len(p) <= MAX_CHARS_PER_LANGUAGE["tr"]
    # Hiçbir karakter kaybolmadı (boşluk + nokta hassaslığı)
    reconstructed = " ".join(pieces)
    # Orijinal metnin tüm kelimeleri parçalarda olmalı
    for word in ["Bismillah", "evladım", "fil", "saati", "Görmek"]:
        assert word in reconstructed


def test_split_respects_custom_limit() -> None:
    text = "Bir, iki, üç. Dört, beş, altı. Yedi sekiz dokuz."
    pieces = split_text_for_xtts(text, "tr", max_chars=15)
    for p in pieces:
        assert len(p) <= 15


def test_split_hard_cuts_long_unbroken_word() -> None:
    text = "a" * 500
    pieces = split_text_for_xtts(text, "tr")
    assert len(pieces) >= 2
    for p in pieces:
        assert len(p) <= MAX_CHARS_PER_LANGUAGE["tr"]
