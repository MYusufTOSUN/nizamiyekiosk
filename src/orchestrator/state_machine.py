"""State machine: sahne durumlarını ve geçişleri yönetir.

Her geçiş valid edilir, invalid geçişte :class:`OrchestratorError` fırlatır.
Her geçişte (varsa) ``listener``'lara :class:`SessionEvent` yayınlanır.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Optional

from src.core.errors import OrchestratorError
from src.core.interfaces import SessionEvent, SessionState
from src.core.logger import get_logger

_log = get_logger(component="orchestrator.state_machine")


EventListener = Callable[[SessionEvent], Awaitable[None]]


VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.WELCOME, SessionState.ERROR},
    SessionState.WELCOME: {SessionState.LISTENING, SessionState.IDLE, SessionState.ERROR},
    SessionState.LISTENING: {
        SessionState.SELECTION,
        SessionState.THINKING,
        SessionState.FAREWELL,
        SessionState.ERROR,
    },
    SessionState.SELECTION: {SessionState.LISTENING, SessionState.THINKING, SessionState.ERROR},
    SessionState.THINKING: {SessionState.SPEAKING, SessionState.ERROR},
    SessionState.SPEAKING: {SessionState.LISTENING, SessionState.FAREWELL, SessionState.ERROR},
    SessionState.FAREWELL: {SessionState.IDLE, SessionState.ERROR},
    SessionState.ERROR: {SessionState.IDLE},
}


class StateMachine:
    """Tek bir aktif session için state makinesi.

    State Değişikliği için :meth:`transition_to` kullan. ``add_listener`` ile
    state değişikliklerini dış dünyaya yayınlayabilirsin.
    """

    def __init__(self, session_id: str, initial_state: SessionState = SessionState.IDLE) -> None:
        self.session_id = session_id
        self._state = initial_state
        self._listeners: list[EventListener] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> SessionState:
        return self._state

    def add_listener(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def can_transition_to(self, new_state: SessionState) -> bool:
        return new_state in VALID_TRANSITIONS.get(self._state, set())

    async def transition_to(
        self,
        new_state: SessionState,
        *,
        data: Optional[dict[str, object]] = None,
    ) -> None:
        async with self._lock:
            if not self.can_transition_to(new_state):
                raise OrchestratorError(
                    "ORC_001",
                    f"Geçersiz geçiş: {self._state.value} → {new_state.value}",
                )
            previous = self._state
            self._state = new_state
            _log.info(
                "state_changed",
                session_id=self.session_id,
                previous=previous.value,
                new=new_state.value,
            )
            event = SessionEvent(
                session_id=self.session_id,
                type="state_changed",
                data={
                    "previous": previous.value,
                    "new": new_state.value,
                    **(data or {}),
                },
            )
            await self._notify(event)

    async def emit(self, event: SessionEvent) -> None:
        """Yardımcı: state değişikliği dışı event yayınla."""
        await self._notify(event)

    async def _notify(self, event: SessionEvent) -> None:
        if not self._listeners:
            return
        await asyncio.gather(
            *(listener(event) for listener in self._listeners),
            return_exceptions=False,
        )


__all__ = ["StateMachine", "VALID_TRANSITIONS", "EventListener"]
