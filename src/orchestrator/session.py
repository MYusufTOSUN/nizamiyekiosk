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
    """In-memory aktif oturum yöneticisi.

    Süre limiti aşan oturumlar otomatik sonlandırılır (``is_expired``).
    Festival için ``recent`` ile son N oturumun özetine ulaşabilirsin.
    """

    def __init__(
        self,
        max_duration_seconds: int = 180,
        retain_history: int = 50,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._active_id: Optional[str] = None
        self.max_duration_seconds = max_duration_seconds
        self.retain_history = retain_history

    def create(self, visitor_metadata: dict[str, object] | None = None) -> Session:
        session = Session(visitor_metadata=visitor_metadata)
        self._sessions[session.session_id] = session
        self._active_id = session.session_id
        self._prune_history()
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

    def is_expired(self, session: Session) -> bool:
        """Maks süreyi aşan oturum mu?"""
        return session.duration_seconds >= self.max_duration_seconds

    def recent(self, limit: int = 20) -> list[Session]:
        return sorted(
            self._sessions.values(),
            key=lambda s: s.started_at,
            reverse=True,
        )[:limit]

    def _prune_history(self) -> None:
        if len(self._sessions) <= self.retain_history:
            return
        # En eski oturumları sil
        sorted_sessions = sorted(self._sessions.values(), key=lambda s: s.started_at)
        excess = len(self._sessions) - self.retain_history
        for s in sorted_sessions[:excess]:
            if s.session_id == self._active_id:
                continue
            self._sessions.pop(s.session_id, None)


__all__ = ["Session", "ConversationTurn", "SessionStore"]
