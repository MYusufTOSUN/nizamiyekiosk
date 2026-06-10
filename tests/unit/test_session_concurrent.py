"""A3 — SessionStore async lock concurrent create."""

from __future__ import annotations

import asyncio

import pytest

from src.orchestrator.session import SessionStore


@pytest.mark.asyncio
async def test_concurrent_create_async_no_lost_sessions() -> None:
    store = SessionStore()
    N = 20
    results = await asyncio.gather(*(store.create_async() for _ in range(N)))
    # Tüm sessions oluşturulmuş ve unique
    ids = {s.session_id for s in results}
    assert len(ids) == N
    # En son oluşturulan session active
    assert store.active is not None
    assert store.active.session_id in ids


@pytest.mark.asyncio
async def test_end_async_releases_active() -> None:
    store = SessionStore()
    s1 = await store.create_async()
    s2 = await store.create_async()
    assert store.active is s2
    await store.end_async(s2.session_id)
    assert store.active is None
    assert store.get(s1.session_id) is s1  # eski session hala history'de


@pytest.mark.asyncio
async def test_sync_create_still_works() -> None:
    """Sync API geriye dönük uyumlu (REPL + testler için)."""
    store = SessionStore()
    s = store.create()
    assert s.session_id
    assert store.active is s
