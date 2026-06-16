"""M6 — LiveLink TCP reset davranışı (gerçek socket açmaz)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.config import UnrealBridgeSection
from src.unreal_bridge.live_link import LiveLinkSceneController


@pytest.mark.asyncio
async def test_reset_tcp_clears_writer() -> None:
    ctl = LiveLinkSceneController(UnrealBridgeSection(provider="live_link"))
    writer = MagicMock()
    ctl._tcp_writer = writer
    ctl._tcp_reader = MagicMock()
    await ctl._reset_tcp()
    assert ctl._tcp_writer is None
    assert ctl._tcp_reader is None
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_reset_tcp_cancels_response_task() -> None:
    ctl = LiveLinkSceneController(UnrealBridgeSection(provider="live_link"))

    async def _forever() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(_forever())
    ctl._response_task = task
    await ctl._reset_tcp()
    assert ctl._response_task is None
    # cancel() event loop'a dönünce etkili olur
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_close_is_safe_without_connection() -> None:
    ctl = LiveLinkSceneController(UnrealBridgeSection(provider="live_link"))
    await ctl.close()  # patlamamalı
