"""Session ve ConversationTurn veri sınıfları + in-memory yönetimi."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.core.interfaces import Intent, SessionState


class ConversationTurn(BaseModel):
    turn_id: str = Field(default_factory=lambda: f"turn_{uuid.uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    visitor_text: str = ""
    visitor_audio_duration_ms: int = 0
    detected_intent: Optional[Intent] = None

    selected_persona: Optional[str] = None
    llm_response: Optional[str] = None
    llm_source: Literal["rag", "generated", "fallback"] = "generated"
    llm_latency_ms: Optional[int] = None

    tts_audio_path: Optional[str] = None
    tts_latency_ms: Optional[int] = None

    total_latency_ms: Optional[int] = None


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    state: SessionState = SessionState.IDLE

    current_persona: Optional[str] = None
    turns: list[ConversationTurn] = Field(default_factory=list)

    visitor_metadata: Optional[dict[str, object]] = None

    @property
    def duration_seconds(self) -> int:
        end = self.ended_at or datetime.utcnow()
        return int((end - self.started_at).total_seconds())

    @property
    def turn_count(self) -> int:
        return len(self.turns)


class SessionStore:
    """Çok basit in-memory aktif oturum tutucu (Phase 1 için yeterli)."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._active_id: Optional[str] = None

    def create(self, visitor_metadata: dict[str, object] | None = None) -> Session:
        session = Session(visitor_metadata=visitor_metadata)
        self._sessions[session.session_id] = session
        self._active_id = session.session_id
        return session

    @property
    def active(self) -> Optional[Session]:
        if self._active_id is None:
            return None
        return self._sessions.get(self._active_id)

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if session and session.ended_at is None:
            session.ended_at = datetime.utcnow()
            session.state = SessionState.IDLE
        if self._active_id == session_id:
            self._active_id = None
        return session

    def clear_active(self) -> None:
        self._active_id = None


__all__ = ["Session", "ConversationTurn", "SessionStore"]
