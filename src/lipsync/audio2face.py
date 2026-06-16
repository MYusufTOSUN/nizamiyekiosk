"""NVIDIA Audio2Face WebSocket istemcisi.

Festival makinesinde Omniverse Audio2Face Streaming kuruludur. Bu istemci
audio chunk'larını WebSocket üzerinden gönderir, 50 Hz ARKit blendshape frame
döndürür.

Geliştirme makinesinde (Omniverse yok) `Audio2FaceLipSync.connect()` hata
verir → orchestrator `lipsync.provider: mock` ile çalışmaya devam eder.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from src.core.errors import LipSyncError
from src.core.interfaces import BlendshapeFrame, LipSyncProvider
from src.core.logger import get_logger
from src.lipsync.mock import ARKIT_BLENDSHAPE_KEYS

_log = get_logger(component="lipsync.audio2face")


class Audio2FaceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8011
    path: str = "/streaming"
    sample_rate: int = 24000     # TTS çıkışı 24 kHz
    audio_format: str = "pcm_s16le"
    connect_timeout_seconds: float = 5.0
    recv_timeout_seconds: float = 0.5
    # M6: yeniden bağlanma
    max_reconnect_attempts: int = 3
    reconnect_backoff_base: float = 0.5  # 0.5, 1.0, 2.0 sn

    model_config = {"extra": "forbid"}


class Audio2FaceLipSync(LipSyncProvider):
    """Audio2Face Streaming WebSocket client.

    Protokol (Omniverse Streamer 1.x):
      - init handshake: ``{"command":"init", "character":..., "sample_rate":24000}``
      - audio frame: binary mesaj (raw PCM)
      - blendshape frame: ``{"timestamp":us, "blendshapes":{key: float, ...}}``

    Her oturum başında ``connect()`` çağrılır. Ağ kopmasında provider yeniden
    bağlanır; başarısızlık halinde ``LipSyncError`` fırlatır → orchestrator
    karakterli sahneyi vedaya çevirir.
    """

    def __init__(self, config: dict[str, Any] | Audio2FaceConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._ws: Any | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize(config: dict[str, Any] | Audio2FaceConfig | None) -> Audio2FaceConfig:
        if config is None:
            return Audio2FaceConfig()
        if isinstance(config, Audio2FaceConfig):
            return config
        return Audio2FaceConfig(**config)

    async def _ensure_connection(self, character_id: str) -> Any:
        if self._ws is not None:
            return self._ws
        async with self._lock:
            if self._ws is not None:
                return self._ws
            try:
                import websockets  # type: ignore[import-not-found]
            except ImportError as exc:
                raise LipSyncError(
                    "LS_001",
                    "websockets kurulu değil. README Phase 5'e bak.",
                    cause=exc,
                ) from exc

            url = f"ws://{self.config.host}:{self.config.port}{self.config.path}"

            # M6: exponential backoff ile birkaç deneme
            ws_temp: Any | None = None
            last_exc: Exception | None = None
            for attempt in range(self.config.max_reconnect_attempts):
                _log.info("a2f_connect", url=url, attempt=attempt + 1)
                try:
                    ws_temp = await asyncio.wait_for(
                        websockets.connect(url, max_size=None),
                        timeout=self.config.connect_timeout_seconds,
                    )
                    break
                except (TimeoutError, OSError) as exc:
                    last_exc = exc
                    if ws_temp is not None:
                        try:
                            await ws_temp.close()
                        except Exception:  # noqa: BLE001
                            pass
                        ws_temp = None
                    backoff = self.config.reconnect_backoff_base * (2**attempt)
                    _log.warning("a2f_connect_retry", attempt=attempt + 1, backoff=backoff)
                    await asyncio.sleep(backoff)
            if ws_temp is None:
                attempts = self.config.max_reconnect_attempts
                raise LipSyncError(
                    "LS_001",
                    f"Audio2Face bağlantı başarısız ({attempts} deneme): {last_exc}",
                    cause=last_exc,
                )

            init_msg = {
                "command": "init",
                "character": character_id,
                "sample_rate": self.config.sample_rate,
                "audio_format": self.config.audio_format,
            }
            try:
                await ws_temp.send(json.dumps(init_msg))
                try:
                    ack = await asyncio.wait_for(ws_temp.recv(), timeout=2.0)
                    _log.info("a2f_handshake_ok", ack=str(ack)[:100])
                except TimeoutError:
                    _log.warning("a2f_handshake_timeout")
            except Exception as exc:  # noqa: BLE001
                # Handshake fail → socket leak'i önle
                try:
                    await ws_temp.close()
                except Exception:  # noqa: BLE001
                    pass
                raise LipSyncError(
                    "LS_001",
                    f"Audio2Face handshake başarısız: {exc}",
                    cause=exc,
                ) from exc

            self._ws = ws_temp
        return self._ws

    async def generate_blendshapes(
        self,
        audio_stream: AsyncIterator[bytes],
        character_id: str,
    ) -> AsyncIterator[BlendshapeFrame]:
        ws = await self._ensure_connection(character_id)

        send_done = asyncio.Event()

        async def sender() -> None:
            try:
                async for chunk in audio_stream:
                    if not chunk:
                        continue
                    await ws.send(chunk)
                await ws.send(json.dumps({"command": "end_of_audio"}))
            finally:
                send_done.set()

        send_task = asyncio.create_task(sender())
        try:
            while not (send_done.is_set() and ws is None):
                try:
                    msg = await asyncio.wait_for(
                        ws.recv(),
                        timeout=self.config.recv_timeout_seconds,
                    )
                except TimeoutError:
                    if send_done.is_set():
                        break
                    continue
                except Exception as exc:  # noqa: BLE001
                    # M6: bağlantı koptu — soketi temizle ki sonraki çağrı
                    # yeniden bağlansın (kopuk ws tekrar kullanılmaz).
                    _log.warning("a2f_recv_failed_reconnect_next", error=str(exc))
                    await self._reset_connection()
                    raise LipSyncError(
                        "LS_002",
                        f"Audio2Face recv hatası: {exc}",
                        cause=exc,
                    ) from exc

                try:
                    frame = self._parse_frame(msg)
                except (ValueError, KeyError) as exc:
                    _log.warning("a2f_bad_frame", error=str(exc))
                    continue
                if frame is None:
                    continue
                yield frame
        finally:
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    @staticmethod
    def _parse_frame(msg: Any) -> BlendshapeFrame | None:
        if isinstance(msg, bytes):
            try:
                msg = msg.decode("utf-8")
            except UnicodeDecodeError:
                return None
        data = json.loads(msg)
        if data.get("type") == "end":
            return None
        ts_us = int(data.get("timestamp", 0))
        bs = data.get("blendshapes") or data.get("values") or {}
        values = {k: float(bs.get(k, 0.0)) for k in ARKIT_BLENDSHAPE_KEYS}
        return BlendshapeFrame(timestamp_ms=ts_us // 1000, values=values)

    async def _reset_connection(self) -> None:
        """Kopuk soketi kapat + None yap → sonraki çağrı yeniden bağlanır."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


__all__ = ["Audio2FaceLipSync", "Audio2FaceConfig"]
