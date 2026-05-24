"""config.yaml okuma ve env override testi."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.core.config import AppConfig, get_config


def test_default_config_loads() -> None:
    cfg = AppConfig()
    assert cfg.app.name == "bilimfest"
    assert cfg.stt.provider == "mock"
    assert cfg.llm.provider == "mock"
    assert cfg.tts.provider == "mock"
    assert cfg.lipsync.provider == "mock"


def test_env_override_provider() -> None:
    with patch.dict(os.environ, {"BFEST__STT__PROVIDER": "whisper_local"}):
        cfg = AppConfig()
        assert cfg.stt.provider == "whisper_local"


def test_get_config_is_cached() -> None:
    a = get_config()
    b = get_config()
    assert a is b
    c = get_config(reload=True)
    assert c is not a
