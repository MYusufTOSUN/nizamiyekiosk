"""BilimFest FastAPI uygulaması.

Phase 1 yapısı:
    - HTTP REST endpoint'leri (``src.orchestrator.routes``)
    - WebSocket ``/ws/audio`` → ConversationPipeline ile uçtan uca akış
    - WebSocket ``/ws/events`` → operatör paneline event broadcast
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from src.core.config import AppConfig, get_config
from src.core.factory import ProviderFactory
from src.core.interfaces import (
    SceneCommand,
    SessionEvent,
    SessionState,
)
from src.core.logger import configure_logging, get_logger
from src.orchestrator.pipeline import ConversationPipeline
from src.orchestrator.routes import router
from src.orchestrator.session import SessionStore
from src.orchestrator.state_machine import StateMachine
from src.orchestrator.watchdog import session_watchdog

_log = get_logger(component="orchestrator.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = get_config()
    configure_logging(
        level=config.metrics.log_level,
        json_format=config.metrics.json_logs,
    )
    _log.info("startup", environment=config.app.environment, version=config.app.version)

    providers = ProviderFactory.build_all(config)
    app.state.config = config
    app.state.providers = providers
    app.state.sessions = SessionStore(
        max_duration_seconds=config.session.max_duration_seconds,
    )
    app.state.state_machines = {}
    app.state.event_listeners: list[asyncio.Queue[SessionEvent]] = []
    app.state.pipeline = ConversationPipeline(
        stt=providers["stt"],  # type: ignore[arg-type]
        intent=providers["intent"],  # type: ignore[arg-type]
        llm=providers["llm"],  # type: ignore[arg-type]
        rag=providers["rag"],  # type: ignore[arg-type]
        tts=providers["tts"],  # type: ignore[arg-type]
        lipsync=providers["lipsync"],  # type: ignore[arg-type]
        scene=providers["scene"],  # type: ignore[arg-type]
        rag_similarity_threshold=config.llm.rag.similarity_threshold,
    )

    # Scene controller'a init komutu
    await providers["scene"].send_command(  # type: ignore[union-attr]
        SceneCommand(
            command="init",
            params={"environment": config.app.environment},
            request_id=uuid.uuid4().hex,
        )
    )

    # Session watchdog — süre limitini aşan oturumu kapatır
    watchdog_task = asyncio.create_task(
        session_watchdog(
            sessions=app.state.sessions,
            state_machines=app.state.state_machines,
        )
    )

    yield

    _log.info("shutdown")
    watchdog_task.cancel()
    try:
        await watchdog_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    for provider in providers.values():
        close = getattr(provider, "close", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:  # noqa: BLE001
                _log.warning("provider_close_failed", error=str(exc))


app = FastAPI(
    title="BilimFest API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket) -> None:
    """Ziyaretçi mikrofonundan PCM stream alır, pipeline'ı çalıştırır."""
    await websocket.accept()
    app_state: Any = websocket.app.state

    sessions: SessionStore = app_state.sessions
    session = sessions.active
    sm: StateMachine | None = None
    if session is None:
        session = sessions.create()
        sm = StateMachine(session.session_id, initial_state=session.state)
        app_state.state_machines[session.session_id] = sm

        async def broadcast(event: SessionEvent) -> None:
            for q in app_state.event_listeners:
                await q.put(event)

        sm.add_listener(broadcast)
        await sm.transition_to(SessionState.WELCOME)
    else:
        sm = app_state.state_machines.get(session.session_id)
        if sm is None:
            sm = StateMachine(session.session_id, initial_state=session.state)
            app_state.state_machines[session.session_id] = sm

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def audio_iter() -> AsyncIterator[bytes]:
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                return
            yield chunk

    async def reader() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is None:
                    text = msg.get("text")
                    if text == "__end__":
                        break
                    continue
                await audio_queue.put(data)
        finally:
            await audio_queue.put(None)

    reader_task = asyncio.create_task(reader())
    try:
        result = await app_state.pipeline.run_turn(
            session=session,
            sm=sm,
            audio_stream=audio_iter(),
        )
        await websocket.send_json(
            {
                "type": "turn_completed",
                "session_id": session.session_id,
                "state": sm.state.value,
                "persona": result.persona_id,
                "transcription": result.transcription.text if result.transcription else "",
                "intent": result.intent.model_dump() if result.intent else None,
                "response": result.llm_response,
                "source": result.llm_source,
                "audio_bytes": result.audio_bytes,
                "blendshape_frames": result.blendshape_frames,
                "latency_ms": result.total_latency_ms,
            }
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        _log.exception("pipeline_error", error=str(exc))
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["app"]
