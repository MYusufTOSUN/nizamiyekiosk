"""HTTP REST endpoint'leri ve operatör paneli için event broadcast WebSocket."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from src.core.interfaces import (
    SceneCommand,
    SessionEvent,
    SessionState,
)
from src.core.logger import get_logger
from src.core.metrics import render_latest, sessions_total
from src.llm.persona import list_personas
from src.orchestrator.session import SessionStore
from src.orchestrator.state_machine import StateMachine

_log = get_logger(component="orchestrator.routes")

router = APIRouter()


def _store(request: Request) -> SessionStore:
    return request.app.state.sessions  # type: ignore[no-any-return]


def _broadcast_listeners(request: Request) -> list[Any]:
    return request.app.state.event_listeners  # type: ignore[no-any-return]


@router.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.get("/api/v1/status")
async def status(request: Request) -> JSONResponse:
    store = _store(request)
    active = store.active
    return JSONResponse(
        {
            "active_session": active.session_id if active else None,
            "state": active.state.value if active else SessionState.IDLE.value,
            "current_persona": active.current_persona if active else None,
        }
    )


@router.get("/api/v1/characters")
async def characters() -> JSONResponse:
    return JSONResponse(
        [
            {
                "id": p.id,
                "name": p.name,
                "era": p.era,
                "expertise": p.expertise,
            }
            for p in list_personas()
        ]
    )


@router.post("/api/v1/session/start")
async def session_start(request: Request) -> JSONResponse:
    store = _store(request)
    session = store.create()
    sm = StateMachine(session.session_id, initial_state=session.state)
    request.app.state.state_machines[session.session_id] = sm  # type: ignore[index]

    # Event listener'ları bind et
    async def broadcast(event: SessionEvent) -> None:
        for queue in _broadcast_listeners(request):
            await queue.put(event)

    sm.add_listener(broadcast)
    await sm.transition_to(SessionState.WELCOME)
    await broadcast(
        SessionEvent(session_id=session.session_id, type="session_started", data={})
    )
    sessions_total.labels(status="started").inc()
    return JSONResponse({"session_id": session.session_id, "state": sm.state.value})


@router.post("/api/v1/session/end")
async def session_end(request: Request) -> JSONResponse:
    store = _store(request)
    active = store.active
    if active is None:
        return JSONResponse({"detail": "No active session"}, status_code=404)
    sm = request.app.state.state_machines.get(active.session_id)  # type: ignore[union-attr]
    if sm is not None:
        if sm.state != SessionState.IDLE:
            try:
                await sm.transition_to(SessionState.FAREWELL)
            except Exception:  # noqa: BLE001
                pass
            try:
                await sm.transition_to(SessionState.IDLE)
            except Exception:  # noqa: BLE001
                pass
    store.end(active.session_id)
    sessions_total.labels(status="completed").inc()
    return JSONResponse({"session_id": active.session_id, "ended": True})


@router.post("/api/v1/emergency_stop")
async def emergency_stop(request: Request) -> JSONResponse:
    scene = request.app.state.providers["scene"]
    await scene.send_command(
        SceneCommand(
            command="emergency_stop",
            params={},
            request_id=uuid.uuid4().hex,
        )
    )
    store = _store(request)
    active = store.active
    if active is not None:
        store.end(active.session_id)
    sessions_total.labels(status="error").inc()
    return JSONResponse({"status": "stopped"})


@router.get("/api/v1/metrics")
async def metrics() -> Response:
    payload, content_type = render_latest()
    return Response(content=payload, media_type=content_type)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=256)
    websocket.app.state.event_listeners.append(queue)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        if queue in websocket.app.state.event_listeners:
            websocket.app.state.event_listeners.remove(queue)


__all__ = ["router"]
