"""BilimFest hata hiyerarşisi.

Tüm domain exception'ları :class:`BilimFestError` türevidir. Her hatanın bir
``component``, ``error_code`` (bkz. :data:`ERROR_CODES`) ve Türkçe
``user_message`` alanı vardır — son kullanıcıya gösterilebilir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path


class BilimFestError(Exception):
    """Tüm BilimFest hatalarının taban sınıfı."""

    component: ClassVar[str] = "unknown"
    default_user_message: ClassVar[str] = "Bir aksilik oldu, biraz sonra tekrar deneyelim."

    def __init__(
        self,
        error_code: str,
        message: str | None = None,
        *,
        user_message: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.error_code = error_code
        self.user_message = user_message or ERROR_CODES.get(error_code, self.default_user_message)
        msg = message or ERROR_CODES.get(error_code, "Unknown error")
        super().__init__(f"[{self.component}:{error_code}] {msg}")
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, str]:
        return {
            "component": self.component,
            "error_code": self.error_code,
            "user_message": self.user_message,
            "message": str(self),
        }


class STTError(BilimFestError):
    component: ClassVar[str] = "stt"


class IntentError(BilimFestError):
    component: ClassVar[str] = "intent"


class LLMError(BilimFestError):
    component: ClassVar[str] = "llm"


class TTSError(BilimFestError):
    component: ClassVar[str] = "tts"


class LipSyncError(BilimFestError):
    component: ClassVar[str] = "lipsync"


class UnrealBridgeError(BilimFestError):
    component: ClassVar[str] = "unreal_bridge"


class OrchestratorError(BilimFestError):
    component: ClassVar[str] = "orchestrator"


class ConfigError(BilimFestError):
    component: ClassVar[str] = "config"


def validate_model_path(raw_path: str, base_dir: str = "data/models") -> Path:
    """Whitelist: model path'i ``base_dir`` altında olmalı.

    Path traversal saldırılarına karşı koruma + erişim sınırlama. Hata
    mesajı kullanıcıya tam path'i ifşa etmez.
    """
    from pathlib import Path

    base = Path(base_dir).resolve()
    resolved = Path(raw_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ConfigError(
            "CFG_001",
            f"Model path izin verilen dizin dışında: {raw_path}",
            user_message="Model konfigürasyonu hatalı, yönetici ile irtibata geç.",
            cause=exc,
        ) from exc
    return resolved


ERROR_CODES: dict[str, str] = {
    # STT
    "STT_001": "Ses tanıma servisi başlamadı",
    "STT_002": "Ses kalitesi yetersiz",
    "STT_003": "Türkçe dışı bir dil algılandı",
    # LLM
    "LLM_001": "Model yüklenemedi",
    "LLM_002": "VRAM yetersiz",
    "LLM_003": "Cevap üretim zaman aşımı",
    "LLM_004": "Safety filter tetiklendi",
    # TTS
    "TTS_001": "Ses sentezi başarısız",
    "TTS_002": "Karakter sesi bulunamadı",
    # LipSync
    "LS_001": "Audio2Face bağlantısı koptu",
    "LS_002": "Blendshape üretim hatası",
    # Unreal Bridge
    "UB_001": "Unreal Engine bağlantısı yok",
    "UB_002": "Sahne komutu reddedildi",
    # Orchestrator
    "ORC_001": "Geçersiz state geçişi",
    "ORC_002": "Aktif oturum bulunamadı",
    "ORC_003": "Oturum süresi doldu",
    # Config
    "CFG_001": "Konfigürasyon dosyası okunamadı",
    "CFG_002": "Bilinmeyen provider",
}
