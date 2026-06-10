"""A2 — Audio2Face connection cleanup on handshake failure (mocked)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.errors import LipSyncError
from src.lipsync.audio2face import Audio2FaceConfig, Audio2FaceLipSync


@pytest.mark.asyncio
async def test_connection_failure_does_not_set_ws() -> None:
    """Connect timeout — self._ws None kalmalı, sonraki çağrı yeniden dener."""
    a2f = Audio2FaceLipSync(Audio2FaceConfig(connect_timeout_seconds=0.01))
    # websockets.connect'i TimeoutError raise edecek şekilde mock'la
    with patch("websockets.connect", side_effect=TimeoutError("boom")):
        with pytest.raises(LipSyncError) as exc:
            await a2f._ensure_connection("cezeri")
    assert exc.value.error_code == "LS_001"
    assert a2f._ws is None  # leak yok


@pytest.mark.asyncio
async def test_handshake_failure_closes_socket() -> None:
    """Handshake exception — ws_temp.close() çağrılmalı."""
    a2f = Audio2FaceLipSync()

    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock(side_effect=RuntimeError("handshake fail"))
    fake_ws.close = AsyncMock()

    async def _fake_connect(*args: Any, **kwargs: Any) -> Any:
        return fake_ws

    with patch("websockets.connect", side_effect=_fake_connect):
        with pytest.raises(LipSyncError) as exc:
            await a2f._ensure_connection("cezeri")

    assert exc.value.error_code == "LS_001"
    fake_ws.close.assert_awaited_once()
    assert a2f._ws is None
