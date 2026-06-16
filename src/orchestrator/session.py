"""Session ve ConversationTurn veri sınıfları + in-memory yönetimi."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.core.interfaces import Intent, SessionState


class ConversationTurn(BaseModel):
    turn_id: str = Field(default_factory=lambda: f"turn_{uuid.uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    visitor_text: str = ""
    visitor_audio_duration_ms: int = 0
    detected_intent: Intent | None = None

    selected_persona: str | None = None
    llm_response: str | None = None
    llm_source: Literal["rag", "generated", "fallback"] = "generated"
    llm_latency_ms: int | None = None

    tts_audio_path: str | None = None
    tts_latency_ms: int | None = None

    total_latency_ms: int | None = None


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    state: SessionState = SessionState.IDLE

    current_persona: str | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)

    visitor_metadata: dict[str, object] | None = None

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

    Thread-safety: ``asyncio.Lock`` ile create/end/prune atomik. Festival'de
    aynı anda iki ziyaretçi WebSocket açarsa race condition olmaz.
    """

    def __init__(
        self,
        max_duration_seconds: int = 180,
        retain_history: int = 50,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._active_id: str | None = None
        self.max_duration_seconds = max_duration_seconds
        self.retain_history = retain_history
        # asyncio.Lock yalnızca event loop içinde init edilebilir; lazy init.
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def create(self, visitor_metadata: dict[str, object] | None = None) -> Session:
        """Synchronous create — basit kullanım için (test/REPL).

        Concurrent WebSocket senaryosunda ``create_async`` tercih edilir.
        """
        session = Session(visitor_metadata=visitor_metadata)
        self._sessions[session.session_id] = session
        self._active_id = session.session_id
        self._prune_history()
        return session

    async def create_async(
        self, visitor_metadata: dict[str, object] | None = None
    ) -> Session:
        """Concurrent-safe session create — async loop içinde kullan."""
        async with self._get_lock():
            session = Session(visitor_metadata=visitor_metadata)
            self._sessions[session.session_id] = session
            self._active_id = session.session_id
            self._prune_history()
            return session

    @property
    def active(self) -> Session | None:
        if self._active_id is None:
            return None
        return self._sessions.get(self._active_id)

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session and session.ended_at is None:
            session.ended_at = datetime.utcnow()
            session.state = SessionState.IDLE
        if self._active_id == session_id:
            self._active_id = None
        return session

    async def end_async(self, session_id: str) -> Session | None:
        async with self._get_lock():
            return self.end(session_id)

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
