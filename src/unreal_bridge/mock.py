"""MockSceneController — komutları belleğe kaydeder, network açmaz."""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.core.interfaces import (
    BlendshapeFrame,
    SceneCommand,
    SceneCommandResponse,
    SceneController,
)
from src.core.logger import get_logger

_log = get_logger(component="unreal_bridge.mock")


class MockSceneController(SceneController):
    """Komut geçmişini ``commands``, blendshape sayısını ``blendshape_count`` tutar."""

    def __init__(self) -> None:
        self.commands: list[SceneCommand] = []
        self.blendshape_count: int = 0

    async def send_command(self, command: SceneCommand) -> SceneCommandResponse:
        self.commands.append(command)
        _log.info("scene_command", command=command.command, request_id=command.request_id)
        return SceneCommandResponse(
            request_id=command.request_id,
            status="ok",
            data={"mock": True},
        )

    async def send_blendshapes(
        self,
        character_id: str,
        frames: AsyncIterator[BlendshapeFrame],
    ) -> None:
        async for frame in frames:
            if frame is None:
                continue
            self.blendshape_count += 1
