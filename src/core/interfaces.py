"""Tüm AI bileşenleri için Abstract Base Class'lar ve veri tipleri.

Bu dosya **abstraction layer**'ın merkezi: STT, LLM, TTS, LipSync, RAG, Scene
Controller arayüzleri burada. İleride lokal ↔ cloud geçişi için her bileşenin
yeni bir implementation'ı bu arayüzleri uygulamak zorunda.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Ortak veri tipleri
# ---------------------------------------------------------------------------

class SessionState(str, Enum):
    """Sahne durumu (state machine)."""

    IDLE = "idle"
    WELCOME = "welcome"
    LISTENING = "listening"
    SELECTION = "selection"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FAREWELL = "farewell"
    ERROR = "error"


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

class TranscriptionResult(BaseModel):
    text: str
    is_final: bool
    confidence: float
    duration_ms: int


class STTProvider(ABC):
    """Speech-to-text provider arayüzü."""

    @abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "tr",
    ) -> AsyncIterator[TranscriptionResult]:
        """Audio chunk stream alır, transcription sonuçları üretir."""
        # ABC: alt sınıflar override edecek. async generator olduğu için
        # tip için boş yield blok yeterli (mypy uyumlu).
        if False:
            yield  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        """Provider kaynaklarını serbest bırak (override edilebilir)."""
        return None


# ---------------------------------------------------------------------------
# Intent Detection
# ---------------------------------------------------------------------------

IntentType = Literal["selection", "question", "farewell", "unclear"]


class Intent(BaseModel):
    type: IntentType
    target: Optional[str] = None  # selection için karakter id: "cezeri", ...
    confidence: float
    raw_text: str


class IntentDetector(ABC):
    """Niyet (selection/question/farewell) tespiti."""

    @abstractmethod
    async def detect(self, text: str, current_state: SessionState) -> Intent: ...


# ---------------------------------------------------------------------------
# LLM + RAG + Persona
# ---------------------------------------------------------------------------

class PersonaConfig(BaseModel):
    id: str
    name: str
    full_name: str = ""
    era: str = ""
    birthplace: str = ""
    expertise: list[str] = Field(default_factory=list)
    famous_works: list[str] = Field(default_factory=list)
    voice_id: str

    system_prompt: str
    safety_fallbacks: dict[str, str] = Field(default_factory=dict)
    rag_collection: str

    initial_greeting: str
    farewell_messages: list[str] = Field(default_factory=list)


class ConversationTurn(BaseModel):
    role: Literal["visitor", "persona"]
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ConversationContext(BaseModel):
    """LLM'e verilen kısa konuşma geçmişi."""

    session_id: str
    persona_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)


class RAGResult(BaseModel):
    response_text: str
    similarity: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGStore(ABC):
    """Persona başına hazır cevap havuzu (vector store)."""

    @abstractmethod
    async def query(
        self,
        question: str,
        persona_id: str,
        top_k: int = 3,
    ) -> list[RAGResult]: ...

    async def close(self) -> None:
        return None


class LLMProvider(ABC):
    """Streaming LLM provider arayüzü."""

    @abstractmethod
    async def generate_response(
        self,
        question: str,
        persona: PersonaConfig,
        context: ConversationContext | None = None,
    ) -> AsyncIterator[str]:
        """Token-by-token streaming çıkış."""
        if False:
            yield  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

class TTSProvider(ABC):
    """Text-to-speech provider arayüzü.

    Çıkış formatı: PCM 16-bit, 24000 Hz, mono. Chunk: 480 sample (20ms).
    """

    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if False:
            yield  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Lip-sync
# ---------------------------------------------------------------------------

class BlendshapeFrame(BaseModel):
    timestamp_ms: int
    values: dict[str, float]  # ARKit 52 blendshape


class LipSyncProvider(ABC):
    """Audio → blendshape stream."""

    @abstractmethod
    async def generate_blendshapes(
        self,
        audio_stream: AsyncIterator[bytes],
        character_id: str,
    ) -> AsyncIterator[BlendshapeFrame]:
        if False:
            yield  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Scene Controller (Unreal Bridge)
# ---------------------------------------------------------------------------

SceneCommandType = Literal[
    "init",
    "show_selection",
    "show_character",
    "hide_character",
    "play_audio",
    "stop_audio",
    "set_lighting",
    "emergency_stop",
    "shutdown",
]


class SceneCommand(BaseModel):
    type: Literal["scene_command"] = "scene_command"
    command: SceneCommandType
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class SceneCommandResponse(BaseModel):
    type: Literal["command_response"] = "command_response"
    request_id: str
    status: Literal["ok", "error"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class SceneController(ABC):
    """Unreal Engine ile sahne kontrol köprüsü."""

    @abstractmethod
    async def send_command(self, command: SceneCommand) -> SceneCommandResponse: ...

    @abstractmethod
    async def send_blendshapes(
        self,
        character_id: str,
        frames: AsyncIterator[BlendshapeFrame],
    ) -> None: ...

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Session Event (operatör paneli için broadcast)
# ---------------------------------------------------------------------------

SessionEventType = Literal[
    "session_started",
    "state_changed",
    "transcription_received",
    "intent_detected",
    "llm_response_started",
    "llm_response_completed",
    "tts_started",
    "tts_completed",
    "error_occurred",
    "session_ended",
]


class SessionEvent(BaseModel):
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: SessionEventType
    data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "SessionState",
    "TranscriptionResult",
    "STTProvider",
    "Intent",
    "IntentType",
    "IntentDetector",
    "PersonaConfig",
    "ConversationTurn",
    "ConversationContext",
    "RAGResult",
    "RAGStore",
    "LLMProvider",
    "TTSProvider",
    "BlendshapeFrame",
    "LipSyncProvider",
    "SceneCommand",
    "SceneCommandResponse",
    "SceneCommandType",
    "SceneController",
    "SessionEvent",
    "SessionEventType",
]
