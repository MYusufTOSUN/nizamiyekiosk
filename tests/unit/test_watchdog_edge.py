"""Watchdog edge case'ler — daha kapsamlı state path coverage."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from src.core.interfaces import SessionState
from src.orchestrator.session import SessionStore
from src.orchestrator.state_machine import StateMachine
from src.orchestrator.watchdog import session_watchdog


@pytest.mark.parametrize(
    "state",
    [
        SessionState.SPEAKING,
        SessionState.LISTENING,
        SessionState.WELCOME,
        SessionState.SELECTION,
        SessionState.THINKING,
    ],
)
@pytest.mark.asyncio
async def test_watchdog_terminates_from_any_active_state(state: SessionState) -> None:
    """Watchdog her aktif state'ten IDLE'a güvenli inebilmeli."""
    store = SessionStore(max_duration_seconds=1)
    s = store.create()
    s.started_at = datetime.utcnow() - timedelta(seconds=2)
    sm = StateMachine(s.session_id)
    # Hedef state'e güvenli yol
    if state == SessionState.WELCOME:
        await sm.transition_to(SessionState.WELCOME)
    elif state == SessionState.LISTENING:
        await sm.transition_to(SessionState.WELCOME)
        await sm.transition_to(SessionState.LISTENING)
    elif state == SessionState.SELECTION:
        await sm.transition_to(SessionState.WELCOME)
        await sm.transition_to(SessionState.LISTENING)
        await sm.transition_to(SessionState.SELECTION)
    elif state == SessionState.THINKING:
        await sm.transition_to(SessionState.WELCOME)
        await sm.transition_to(SessionState.LISTENING)
        await sm.transition_to(SessionState.THINKING)
    elif state == SessionState.SPEAKING:
        await sm.transition_to(SessionState.WELCOME)
        await sm.transition_to(SessionState.LISTENING)
        await sm.transition_to(SessionState.THINKING)
        await sm.transition_to(SessionState.SPEAKING)

    task = asyncio.create_task(
        session_watchdog(store, {s.session_id: sm}, interval_seconds=0.05)
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert sm.state == SessionState.IDLE
    assert store.active is None


@pytest.mark.asyncio
async def test_watchdog_no_active_session_does_nothing() -> None:
    store = SessionStore(max_duration_seconds=1)
    task = asyncio.create_task(
        session_watchdog(store, {}, interval_seconds=0.05)
    )
    await asyncio.sleep(0.12)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Sorun yok
    assert store.active is None
