"""Pydantic Settings tabanlı konfigürasyon yönetimi.

``config.yaml`` dosyasından okur, ``BFEST__<section>__<key>`` env değişkenleri
ile override edilebilir (nested için ``__`` delimiter).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from src.core.errors import ConfigError


class AppSection(BaseModel):
    name: str = "bilimfest"
    version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"


class AudioInput(BaseModel):
    device: str = "default"
    sample_rate: int = 16000
    channels: int = 1


class AudioOutput(BaseModel):
    device: str = "default"
    sample_rate: int = 24000
    channels: int = 6  # 5.1 surround


class AudioSection(BaseModel):
    input: AudioInput = Field(default_factory=AudioInput)
    output: AudioOutput = Field(default_factory=AudioOutput)


class STTSection(BaseModel):
    provider: str = "mock"
    config: dict[str, Any] = Field(default_factory=dict)


class LLMRAGSection(BaseModel):
    enabled: bool = True
    similarity_threshold: float = 0.85
    # Margin gate: top1 hit sayılması için top1 − top2 farkı bu kadar olmalı
    # (kararsız/yakın eşleşmelerde LLM'e düş — alakasız hazır cevabı engeller).
    margin: float = 0.04
    store_path: str = "data/responses/vector_store"
    candidate_pool: int = 24
    use_reranker: bool = False
    reranker_model: str = "data/models/reranker/bge-reranker-v2-m3"
    # e5 embedder cihazı. 8 GB laptop'ta "cpu" ZORUNLU: Whisper (CTranslate2) ile
    # torch cuDNN sürüm çakışması, e5'in ilk GPU inference'ında süreci C seviyesinde
    # çökertir (Python except yakalayamaz). CPU'da tek kısa sorgu ~300ms — tur başına
    # bir kez, kritik yolda değil; ayrıca ~2.2 GB VRAM'i Llama'ya bırakır.
    # Sergi makinesi (16+ GB, tek cuDNN) "cuda" kullanabilir.
    embedding_device: str = "cuda"


class LLMSection(BaseModel):
    provider: str = "mock"
    config: dict[str, Any] = Field(default_factory=dict)
    rag: LLMRAGSection = Field(default_factory=LLMRAGSection)


class TTSSection(BaseModel):
    provider: str = "mock"
    config: dict[str, Any] = Field(default_factory=dict)


class LipSyncSection(BaseModel):
    provider: str = "mock"
    config: dict[str, Any] = Field(default_factory=dict)


class UnrealBridgeSection(BaseModel):
    provider: str = "mock"
    udp_host: str = "127.0.0.1"
    udp_port: int = 12345
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 12346


class SessionSection(BaseModel):
    max_duration_seconds: int = 180
    silence_timeout_seconds: int = 15


class MetricsSection(BaseModel):
    prometheus_port: int = 9090
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_logs: bool = True


def _project_root() -> Path:
    # src/core/config.py  → parents[2] = proje kökü
    return Path(__file__).resolve().parents[2]


def _default_yaml_path() -> Path:
    """Aktif config dosyasının yolu.

    ``BFEST_CONFIG`` env değişkeni ayarlıysa onu kullanır (örn. sergi
    makinesinde ``config.production.yaml``); aksi halde proje kökündeki
    ``config.yaml``. Mutlak veya köke göre rölatif path kabul eder.
    """
    import os

    override = os.environ.get("BFEST_CONFIG")
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = _project_root() / p
        return p
    return _project_root() / "config.yaml"


class _YamlSource(PydanticBaseSettingsSource):
    """config.yaml dosyasını okuyan settings kaynağı."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self.yaml_path = yaml_path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        # Field-by-field okuma kullanılmıyor, __call__ tüm dosyayı döndürüyor.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self.yaml_path.exists():
            import os

            # BFEST_CONFIG açıkça verildiyse ve dosya yoksa SESSİZ KALMA — patla.
            if os.environ.get("BFEST_CONFIG"):
                raise ConfigError(
                    "CFG_001",
                    f"BFEST_CONFIG belirtildi ama dosya yok: {self.yaml_path}",
                    user_message="Konfigürasyon dosyası bulunamadı, yönetici ile irtibata geç.",
                )
            return {}
        try:
            with self.yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ConfigError("CFG_001", f"config.yaml kökü dict olmalı: {self.yaml_path}")
            return data
        except yaml.YAMLError as exc:
            raise ConfigError("CFG_001", f"config.yaml parse hatası: {exc}", cause=exc) from exc


class AppConfig(BaseSettings):
    """Uygulama konfigürasyonu — config.yaml + env override."""

    app: AppSection = Field(default_factory=AppSection)
    audio: AudioSection = Field(default_factory=AudioSection)
    stt: STTSection = Field(default_factory=STTSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    tts: TTSSection = Field(default_factory=TTSSection)
    lipsync: LipSyncSection = Field(default_factory=LipSyncSection)
    unreal_bridge: UnrealBridgeSection = Field(default_factory=UnrealBridgeSection)
    session: SessionSection = Field(default_factory=SessionSection)
    metrics: MetricsSection = Field(default_factory=MetricsSection)

    model_config = SettingsConfigDict(
        env_prefix="BFEST__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_source = _YamlSource(settings_cls, _default_yaml_path())
        # Öncelik: init > env > .env > yaml > secrets
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


_cached: AppConfig | None = None


def get_config(reload: bool = False) -> AppConfig:
    """Süreç-ömrü boyunca tek bir AppConfig örneği döndürür."""
    global _cached
    if _cached is None or reload:
        _cached = AppConfig()
    return _cached


__all__ = [
    "AppConfig",
    "AppSection",
    "AudioSection",
    "AudioInput",
    "AudioOutput",
    "STTSection",
    "LLMSection",
    "LLMRAGSection",
    "TTSSection",
    "LipSyncSection",
    "UnrealBridgeSection",
    "SessionSection",
    "MetricsSection",
    "get_config",
]
