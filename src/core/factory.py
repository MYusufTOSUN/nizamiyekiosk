"""Provider factory — config'ten implementation seçer.

Tek satır config değişikliği ile lokal ↔ cloud geçişi için tek nokta.
Lazy import: gerçek modeller (Whisper, Llama, XTTS) sadece o provider
seçildiğinde yüklenir.
"""

from __future__ import annotations

from src.core.config import (
    AppConfig,
    LLMSection,
    LipSyncSection,
    STTSection,
    TTSSection,
    UnrealBridgeSection,
)
from src.core.errors import ConfigError
from src.core.interfaces import (
    IntentDetector,
    LLMProvider,
    LipSyncProvider,
    RAGStore,
    SceneController,
    STTProvider,
    TTSProvider,
)


class ProviderFactory:
    """Tüm provider'ları config'e göre kuran factory."""

    # --- STT ---------------------------------------------------------------

    @staticmethod
    def create_stt(cfg: STTSection) -> STTProvider:
        if cfg.provider == "mock":
            from src.stt.mock import MockSTT

            return MockSTT()
        if cfg.provider == "whisper_local":
            from src.stt.whisper_local import WhisperLocalSTT  # type: ignore[import-not-found]

            return WhisperLocalSTT(cfg.config)
        if cfg.provider == "deepgram_cloud":
            from src.stt.deepgram_cloud import DeepgramCloudSTT  # type: ignore[import-not-found]

            return DeepgramCloudSTT(cfg.config)
        raise ConfigError("CFG_002", f"Bilinmeyen STT provider: {cfg.provider}")

    # --- LLM ---------------------------------------------------------------

    @staticmethod
    def create_llm(cfg: LLMSection) -> LLMProvider:
        if cfg.provider == "mock":
            from src.llm.mock import MockLLM

            return MockLLM()
        if cfg.provider == "llama_local":
            from src.llm.llama_local import LlamaLocalLLM  # type: ignore[import-not-found]

            return LlamaLocalLLM(cfg.config)
        if cfg.provider == "claude_cloud":
            from src.llm.claude_cloud import ClaudeCloudLLM  # type: ignore[import-not-found]

            return ClaudeCloudLLM(cfg.config)
        raise ConfigError("CFG_002", f"Bilinmeyen LLM provider: {cfg.provider}")

    @staticmethod
    def create_rag(cfg: LLMSection) -> RAGStore:
        if not cfg.rag.enabled:
            from src.llm.rag_mock import MockRAGStore

            return MockRAGStore(empty=True)
        if cfg.provider == "mock":
            from src.llm.rag_mock import MockRAGStore

            return MockRAGStore()
        # Gerçek RAG store Phase 3'te gelecek.
        from src.llm.rag_mock import MockRAGStore

        return MockRAGStore()

    # --- TTS ---------------------------------------------------------------

    @staticmethod
    def create_tts(cfg: TTSSection) -> TTSProvider:
        if cfg.provider == "mock":
            from src.tts.mock import MockTTS

            return MockTTS()
        if cfg.provider == "xtts_local":
            from src.tts.xtts_local import XTTSLocal  # type: ignore[import-not-found]

            return XTTSLocal(cfg.config)
        if cfg.provider == "elevenlabs_cloud":
            from src.tts.elevenlabs_cloud import ElevenLabsCloudTTS  # type: ignore[import-not-found]

            return ElevenLabsCloudTTS(cfg.config)
        raise ConfigError("CFG_002", f"Bilinmeyen TTS provider: {cfg.provider}")

    # --- Lip-sync ----------------------------------------------------------

    @staticmethod
    def create_lipsync(cfg: LipSyncSection) -> LipSyncProvider:
        if cfg.provider == "mock":
            from src.lipsync.mock import MockLipSync

            return MockLipSync()
        if cfg.provider == "audio2face":
            from src.lipsync.audio2face import Audio2FaceLipSync  # type: ignore[import-not-found]

            return Audio2FaceLipSync(cfg.config)
        raise ConfigError("CFG_002", f"Bilinmeyen LipSync provider: {cfg.provider}")

    # --- Unreal Bridge -----------------------------------------------------

    @staticmethod
    def create_scene_controller(cfg: UnrealBridgeSection) -> SceneController:
        if cfg.provider == "mock":
            from src.unreal_bridge.mock import MockSceneController

            return MockSceneController()
        if cfg.provider == "live_link":
            from src.unreal_bridge.live_link import LiveLinkSceneController  # type: ignore[import-not-found]

            return LiveLinkSceneController(cfg)
        raise ConfigError("CFG_002", f"Bilinmeyen Unreal bridge provider: {cfg.provider}")

    # --- Intent ------------------------------------------------------------

    @staticmethod
    def create_intent_detector() -> IntentDetector:
        from src.intent.detector import KeywordIntentDetector

        return KeywordIntentDetector()

    # --- Top-level ---------------------------------------------------------

    @classmethod
    def build_all(cls, config: AppConfig) -> dict[str, object]:
        """Tüm provider'ları kur ve dict döndür (app.state'e yüklenir)."""
        return {
            "stt": cls.create_stt(config.stt),
            "llm": cls.create_llm(config.llm),
            "rag": cls.create_rag(config.llm),
            "tts": cls.create_tts(config.tts),
            "lipsync": cls.create_lipsync(config.lipsync),
            "scene": cls.create_scene_controller(config.unreal_bridge),
            "intent": cls.create_intent_detector(),
        }


__all__ = ["ProviderFactory"]
