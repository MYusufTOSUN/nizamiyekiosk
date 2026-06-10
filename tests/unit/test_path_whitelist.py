"""validate_model_path A5 testleri — path traversal koruması."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.errors import ConfigError, validate_model_path


def test_valid_path_inside_data_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "data" / "models"
    sub = base / "llama" / "test.gguf"
    sub.parent.mkdir(parents=True)
    sub.write_text("x")
    monkeypatch.chdir(tmp_path)
    result = validate_model_path("data/models/llama/test.gguf")
    assert result.exists()
    assert result.is_relative_to(base.resolve())


def test_path_traversal_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "data" / "models").mkdir(parents=True)
    (tmp_path / "evil.txt").write_text("steal")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc:
        validate_model_path("../../evil.txt")
    assert exc.value.error_code == "CFG_001"
    # user_message tam path leak etmez
    assert "/etc" not in exc.value.user_message
    assert exc.value.user_message.startswith("Model konfigürasyonu")


def test_absolute_path_outside_base_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data" / "models").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        # Windows için forward-slash de çalışmalı
        validate_model_path("C:/Windows/System32/cmd.exe")


def test_custom_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "alt_models"
    base.mkdir()
    (base / "x.bin").write_text("x")
    monkeypatch.chdir(tmp_path)
    result = validate_model_path("alt_models/x.bin", base_dir="alt_models")
    assert result.exists()
