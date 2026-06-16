"""Session watchdog — süre limitini aşan oturumu vedaya/IDLE'a alır.

FastAPI lifespan içinde arka planda çalışır. Aktif oturumun ``duration_seconds``
``max_duration_seconds`` aşarsa state machine'i ERROR → IDLE veya
SPEAKING/LISTENING → FAREWELL → IDLE'a sürer.
"""

from __future__ import annotations

import asyncio

from src.core.interfaces import SessionState
from src.core.logger import get_logger
from src.core.metrics import sessions_total
from src.orchestrator.session import SessionStore
from src.orchestrator.state_machine import StateMachine

_log = get_logger(component="orchestrator.watchdog")


async def session_watchdog(
    sessions: SessionStore,
    state_machines: dict[str, StateMachine],
    interval_seconds: float = 1.0,
) -> None:
    """Periyodik kontrol — aktif oturum süresi aşıldıysa zorla bitir."""
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            active = sessions.active
            if active is None:
                continue
            if not sessions.is_expired(active):
                continue

            sm = state_machines.get(active.session_id)
            if sm is None:
                sessions.end(active.session_id)
                continue

            _log.info(
                "session_expired",
                session_id=active.session_id,
                duration_s=active.duration_seconds,
                state=sm.state.value,
            )
            # Süre aşımı = ziyaretçi terk etti / sergici geçti say
            sessions_total.labels(status="abandoned").inc()
            await _terminate(
                sm, sessions, session_id=active.session_id, state_machines=state_machines
            )
    except asyncio.CancelledError:
        return


async def _terminate(
    sm: StateMachine,
    sessions: SessionStore,
    session_id: str,
    state_machines: dict[str, StateMachine] | None = None,
) -> None:
    """State akışını güvenli şekilde IDLE'a çek."""
    current = sm.state
    try:
        if current == SessionState.SPEAKING or current == SessionState.LISTENING:
            await sm.transition_to(SessionState.FAREWELL)
            await sm.transition_to(SessionState.IDLE)
        elif current in (
            SessionState.WELCOME,
            SessionState.SELECTION,
            SessionState.THINKING,
        ):
            # Geçersiz yollar — ERROR → IDLE en güvenli
            await sm.transition_to(SessionState.ERROR)
            await sm.transition_to(SessionState.IDLE)
        elif current == SessionState.IDLE:
            pass
    except Exception as exc:  # noqa: BLE001
        _log.warning("watchdog_transition_failed", error=str(exc), state=current.value)
    finally:
        sessions.end(session_id)
        if state_machines is not None:  # M7: sm sızıntısını önle
            state_machines.pop(session_id, None)


__all__ = ["session_watchdog"]
