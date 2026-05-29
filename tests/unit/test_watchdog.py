"""Session watchdog + SessionStore expiry testleri."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from src.core.interfaces import SessionState
from src.orchestrator.session import SessionStore
from src.orchestrator.state_machine import StateMachine
from src.orchestrator.watchdog import session_watchdog


def test_session_not_expired_initially() -> None:
    store = SessionStore(max_duration_seconds=180)
    s = store.create()
    assert not store.is_expired(s)


def test_session_expired_after_age() -> None:
    store = SessionStore(max_duration_seconds=1)
    s = store.create()
    s.started_at = datetime.utcnow() - timedelta(seconds=2)
    assert store.is_expired(s)


def test_retain_history_caps_sessions() -> None:
    store = SessionStore(max_duration_seconds=10, retain_history=3)
    created = []
    for _ in range(5):
        created.append(store.create())
    # Yalnızca 3 oturum saklanır (en yenisi aktif)
    assert len(store.recent(limit=10)) <= 4  # +1 active not pruned


def test_recent_sorted_by_started_at_desc() -> None:
    store = SessionStore()
    a = store.create()
    b = store.create()
    c = store.create()
    # Hızlı ardışık create'lerde datetime aynı olabilir — net sıralama için
    # zaman damgalarını belirgin yap
    a.started_at = datetime.utcnow() - timedelta(seconds=3)
    b.started_at = datetime.utcnow() - timedelta(seconds=2)
    c.started_at = datetime.utcnow() - timedelta(seconds=1)
    rec = store.recent()
    assert rec[0].session_id == c.session_id
    assert rec[1].session_id == b.session_id
    assert rec[2].session_id == a.session_id


@pytest.mark.asyncio
async def test_watchdog_terminates_expired_listening_session() -> None:
    store = SessionStore(max_duration_seconds=1)
    s = store.create()
    s.started_at = datetime.utcnow() - timedelta(seconds=2)
    sm = StateMachine(s.session_id, initial_state=SessionState.IDLE)
    await sm.transition_to(SessionState.WELCOME)
    await sm.transition_to(SessionState.LISTENING)

    state_machines = {s.session_id: sm}
    task = asyncio.create_task(
        session_watchdog(store, state_machines, interval_seconds=0.05)
    )
    # Watchdog'a 1 çevrim tanı
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert sm.state == SessionState.IDLE
    assert store.active is None


@pytest.mark.asyncio
async def test_watchdog_ignores_active_unexpired_session() -> None:
    store = SessionStore(max_duration_seconds=60)
    s = store.create()
    sm = StateMachine(s.session_id, initial_state=SessionState.IDLE)
    await sm.transition_to(SessionState.WELCOME)
    state_machines = {s.session_id: sm}

    task = asyncio.create_task(
        session_watchdog(store, state_machines, interval_seconds=0.05)
    )
    await asyncio.sleep(0.12)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert sm.state == SessionState.WELCOME  # değişmedi
    assert store.active is s
