"""ProviderFactory: mock yolu yanıt veriyor, bilinmeyen provider hata fırlatıyor."""

from __future__ import annotations

import pytest

from src.core.config import (
    LLMSection,
    LipSyncSection,
    STTSection,
    TTSSection,
    UnrealBridgeSection,
)
from src.core.errors import ConfigError
from src.core.factory import ProviderFactory
from src.core.interfaces import (
    IntentDetector,
    LLMProvider,
    LipSyncProvider,
    RAGStore,
    SceneController,
    STTProvider,
    TTSProvider,
)


def test_factory_creates_mock_stt() -> None:
    assert isinstance(ProviderFactory.create_stt(STTSection(provider="mock")), STTProvider)


def test_factory_creates_mock_llm() -> None:
    assert isinstance(ProviderFactory.create_llm(LLMSection(provider="mock")), LLMProvider)


def test_factory_creates_mock_tts() -> None:
    assert isinstance(ProviderFactory.create_tts(TTSSection(provider="mock")), TTSProvider)


def test_factory_creates_mock_lipsync() -> None:
    assert isinstance(
        ProviderFactory.create_lipsync(LipSyncSection(provider="mock")), LipSyncProvider
    )


def test_factory_creates_mock_scene() -> None:
    assert isinstance(
        ProviderFactory.create_scene_controller(UnrealBridgeSection(provider="mock")),
        SceneController,
    )


def test_factory_creates_intent_detector() -> None:
    assert isinstance(ProviderFactory.create_intent_detector(), IntentDetector)


def test_factory_creates_rag() -> None:
    assert isinstance(ProviderFactory.create_rag(LLMSection(provider="mock")), RAGStore)


def test_factory_unknown_provider_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        ProviderFactory.create_stt(STTSection(provider="banana"))
    assert exc.value.error_code == "CFG_002"
