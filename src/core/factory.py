"""Provider factory — config'ten implementation seçer.

Tek satır config değişikliği ile lokal ↔ cloud geçişi için tek nokta.
Lazy import: gerçek modeller (Whisper, Llama, XTTS) sadece o provider
seçildiğinde yüklenir.
"""

from __future__ import annotations

from src.core.config import (
    AppConfig,
    LipSyncSection,
    LLMSection,
    STTSection,
    TTSSection,
    UnrealBridgeSection,
)
from src.core.errors import ConfigError
from src.core.interfaces import (
    IntentDetector,
    LipSyncProvider,
    LLMProvider,
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

            return DeepgramCloudSTT(cfg.config)  # type: ignore[no-any-return]
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

            return ClaudeCloudLLM(cfg.config)  # type: ignore[no-any-return]
        raise ConfigError("CFG_002", f"Bilinmeyen LLM provider: {cfg.provider}")

    @staticmethod
    def create_rag(cfg: LLMSection) -> RAGStore:
        if not cfg.rag.enabled:
            from src.llm.rag_mock import MockRAGStore

            return MockRAGStore(empty=True)
        if cfg.provider == "mock":
            from src.llm.rag_mock import MockRAGStore

            return MockRAGStore()
        # Phase 3+ gerçek RAG: ChromaDB + multilingual embedding.
        from src.llm.rag_store import ChromaRAGStore

        return ChromaRAGStore(
            {
                "store_path": cfg.rag.store_path,
                "candidate_pool": cfg.rag.candidate_pool,
                "use_reranker": cfg.rag.use_reranker,
                "reranker_model": cfg.rag.reranker_model,
                "embedding_device": cfg.rag.embedding_device,
            }
        )

    # --- TTS ---------------------------------------------------------------

    @staticmethod
    def create_tts(cfg: TTSSection) -> TTSProvider:
        if cfg.provider == "mock":
            from src.tts.mock import MockTTS

            return MockTTS()
        if cfg.provider == "xtts_local":
            from src.tts.xtts_local import XTTSLocalTTS

            return XTTSLocalTTS(cfg.config)
        if cfg.provider == "elevenlabs_cloud":
            from src.tts.elevenlabs_cloud import (
                ElevenLabsCloudTTS,  # type: ignore[import-not-found]
            )

            return ElevenLabsCloudTTS(cfg.config)  # type: ignore[no-any-return]
        raise ConfigError("CFG_002", f"Bilinmeyen TTS provider: {cfg.provider}")

    # --- Lip-sync ----------------------------------------------------------

    @staticmethod
    def create_lipsync(cfg: LipSyncSection) -> LipSyncProvider:
        if cfg.provider == "mock":
            from src.lipsync.mock import MockLipSync

            return MockLipSync()
        if cfg.provider == "audio2face":
            from src.lipsync.audio2face import Audio2FaceLipSync

            return Audio2FaceLipSync(cfg.config)
        raise ConfigError("CFG_002", f"Bilinmeyen LipSync provider: {cfg.provider}")

    # --- Unreal Bridge -----------------------------------------------------

    @staticmethod
    def create_scene_controller(cfg: UnrealBridgeSection) -> SceneController:
        if cfg.provider == "mock":
            from src.unreal_bridge.mock import MockSceneController

            return MockSceneController()
        if cfg.provider == "live_link":
            from src.unreal_bridge.live_link import (
                LiveLinkSceneController,  # type: ignore[import-not-found]
            )

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


def validate_startup(config: AppConfig) -> list[str]:
    """M5: lifespan'de fail-fast doğrulama.

    Seçili (mock olmayan) provider'lar için model/dosya varlığını kontrol et.
    Eksikleri liste olarak döndürür. ``app.environment == 'production'`` ise
    çağıran taraf bu liste boş değilse açılmayı reddetmeli.
    """
    from pathlib import Path

    problems: list[str] = []

    def _check(path_str: str, label: str) -> None:
        if path_str and not Path(path_str).exists():
            problems.append(f"{label}: {path_str} yok")

    if config.stt.provider == "whisper_local":
        _check(str(config.stt.config.get("model", "")), "STT model")
    if config.llm.provider == "llama_local":
        _check(str(config.llm.config.get("model_path", "")), "LLM model")
        if config.llm.rag.enabled:
            _check(
                "data/models/embeddings/multilingual-e5-large", "RAG embedder"
            )
    if config.tts.provider == "xtts_local":
        mp = str(config.tts.config.get("model_path", ""))
        if mp:
            _check(str(Path(mp) / "model.pth"), "XTTS model.pth")
    return problems


__all__ = ["ProviderFactory", "validate_startup"]
