"""Unreal Engine 5 Live Link köprüsü.

İki kanal:
  - **UDP** (port 12345): blendshape frame'leri 50 Hz'de — binary protokol
    (``protocol.pack_blendshape_packet``).
  - **TCP** (port 12346): sahne kontrol komutları — JSON, satır sonlandırıcı.

Festival makinesinde Unreal MetaHuman blueprint'i bu iki socket'i dinler.
Coworker'ın Unreal projesi paralel geliştirilir; protokol `api_contracts.md`'ye
sabitlenmiştir.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

from src.core.config import UnrealBridgeSection
from src.core.errors import UnrealBridgeError
from src.core.interfaces import (
    BlendshapeFrame,
    SceneCommand,
    SceneCommandResponse,
    SceneController,
)
from src.core.logger import get_logger
from src.unreal_bridge.protocol import pack_blendshape_packet

_log = get_logger(component="unreal_bridge.live_link")


class LiveLinkSceneController(SceneController):
    """UDP blendshape sender + TCP JSON komut köprüsü.

    Bağlantı kurulamazsa fail-safe: hata fırlatır, orchestrator sergiyi
    güvenli IDLE'a alabilir.
    """

    def __init__(self, config: UnrealBridgeSection | dict[str, Any]) -> None:
        if isinstance(config, dict):
            config = UnrealBridgeSection(**config)
        self.config = config

        # UDP socket — non-blocking, bağlantısız
        self._udp_socket: socket.socket | None = None
        # TCP reader/writer pair
        self._tcp_reader: asyncio.StreamReader | None = None
        self._tcp_writer: asyncio.StreamWriter | None = None
        self._tcp_lock = asyncio.Lock()
        self._pending_responses: dict[str, asyncio.Future[SceneCommandResponse]] = {}
        self._response_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_udp_socket(self) -> socket.socket:
        if self._udp_socket is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            self._udp_socket = sock
        return self._udp_socket

    async def _ensure_tcp(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._tcp_reader is not None and self._tcp_writer is not None:
            return self._tcp_reader, self._tcp_writer
        async with self._tcp_lock:
            if self._tcp_reader is not None and self._tcp_writer is not None:
                return self._tcp_reader, self._tcp_writer
            try:
                self._tcp_reader, self._tcp_writer = await asyncio.wait_for(
                    asyncio.open_connection(self.config.tcp_host, self.config.tcp_port),
                    timeout=3.0,
                )
            except (TimeoutError, OSError) as exc:
                raise UnrealBridgeError(
                    "UB_001",
                    f"Unreal TCP bağlantı başarısız: {exc}",
                    cause=exc,
                ) from exc
            self._response_task = asyncio.create_task(self._response_reader())
            _log.info(
                "tcp_connected",
                host=self.config.tcp_host,
                port=self.config.tcp_port,
            )
            reader, writer = self._tcp_reader, self._tcp_writer
            assert reader is not None and writer is not None
            return reader, writer

    async def _response_reader(self) -> None:
        """TCP cevap dinleyici — request_id ile pending future'ı çözer."""
        assert self._tcp_reader is not None
        try:
            while True:
                line = await self._tcp_reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    _log.warning("tcp_bad_json", error=str(exc), raw=line[:80])
                    continue
                rid = data.get("request_id")
                fut = self._pending_responses.pop(rid, None) if rid else None
                response = SceneCommandResponse(
                    request_id=rid or "",
                    status=data.get("status", "ok"),
                    data=data.get("data") or {},
                    error_message=data.get("error_message"),
                )
                if fut is not None and not fut.done():
                    fut.set_result(response)
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001
            _log.warning("tcp_response_reader_error", error=str(exc))
        finally:
            # M6: reader bitti = bağlantı koptu; bekleyen future'ları çöz + temizle
            for fut in self._pending_responses.values():
                if not fut.done():
                    fut.cancel()
            self._pending_responses.clear()

    async def _reset_tcp(self) -> None:
        """Kopuk TCP bağlantısını temizle → sonraki komut yeniden bağlanır."""
        if self._response_task is not None:
            self._response_task.cancel()
            self._response_task = None
        writer = self._tcp_writer
        self._tcp_writer = None
        self._tcp_reader = None
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # SceneController API
    # ------------------------------------------------------------------

    async def send_command(self, command: SceneCommand) -> SceneCommandResponse:
        _, writer = await self._ensure_tcp()
        msg = command.model_dump(mode="json")
        line = json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[SceneCommandResponse] = loop.create_future()
        self._pending_responses[command.request_id] = fut

        try:
            writer.write(line)
            await writer.drain()
        except (ConnectionError, OSError) as exc:
            self._pending_responses.pop(command.request_id, None)
            # M6: kopuk writer'ı temizle ki sonraki komut yeniden bağlansın
            await self._reset_tcp()
            raise UnrealBridgeError(
                "UB_002",
                f"Komut gönderimi başarısız: {exc}",
                cause=exc,
            ) from exc

        try:
            return await asyncio.wait_for(fut, timeout=2.0)
        except TimeoutError:
            self._pending_responses.pop(command.request_id, None)
            # Cevap gelmedi — yine de devam etmek için generic OK döndür.
            _log.warning(
                "tcp_command_timeout",
                command=command.command,
                request_id=command.request_id,
            )
            return SceneCommandResponse(
                request_id=command.request_id,
                status="ok",
                data={"warning": "no_response"},
            )

    async def send_blendshapes(
        self,
        character_id: str,
        frames: AsyncIterator[BlendshapeFrame],
    ) -> None:
        sock = self._ensure_udp_socket()
        loop = asyncio.get_running_loop()
        host = self.config.udp_host
        port = self.config.udp_port
        sent = 0
        async for frame in frames:
            packet = pack_blendshape_packet(
                timestamp_us=frame.timestamp_ms * 1000,
                character_id=character_id,
                values=frame.values,
            )
            try:
                await loop.sock_sendto(sock, packet, (host, port))
            except OSError as exc:
                # Tek pakete patlamak yerine session'ı kesintiye götürmemek için
                # hatayı logla ve devam et — UDP zaten unreliable.
                _log.warning("udp_send_failed", error=str(exc))
                continue
            sent += 1
        _log.info("blendshape_batch_sent", character=character_id, frames=sent)

    async def close(self) -> None:
        if self._response_task is not None:
            self._response_task.cancel()
            try:
                await self._response_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._response_task = None
        if self._tcp_writer is not None:
            try:
                self._tcp_writer.close()
                await self._tcp_writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._tcp_writer = None
            self._tcp_reader = None
        if self._udp_socket is not None:
            try:
                self._udp_socket.close()
            except OSError:
                pass
            self._udp_socket = None


__all__ = ["LiveLinkSceneController"]
