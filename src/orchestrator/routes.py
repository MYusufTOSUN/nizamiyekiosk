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


def _drop_state_machine(request: Request, session_id: str) -> None:
    """M7: oturum bitince state machine'i dict'ten sil (bellek sızıntısı önler)."""
    request.app.state.state_machines.pop(session_id, None)


@router.get("/health")
async def health() -> JSONResponse:
    """Sığ liveness — süreç ayakta mı."""
    return JSONResponse({"status": "ok"})


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """M15: derin readiness — providerlar yüklü mü, pipeline hazır mı.

    Hazır değilse 503 döner; load balancer / operatör buna bakar.
    """
    app_state = request.app.state
    checks: dict[str, bool] = {}
    providers = getattr(app_state, "providers", {}) or {}
    for name in ("stt", "llm", "tts", "lipsync", "scene", "rag", "intent"):
        checks[name] = name in providers and providers[name] is not None
    checks["pipeline"] = getattr(app_state, "pipeline", None) is not None
    all_ok = all(checks.values())
    return JSONResponse(
        {"status": "ready" if all_ok else "not_ready", "checks": checks},
        status_code=200 if all_ok else 503,
    )


@router.get("/api/v1/status")
async def status(request: Request) -> JSONResponse:
    store = _store(request)
    active = store.active
    # Gerçek durum state machine'de (session.state pipeline'da senkron tutulmaz).
    sm = request.app.state.state_machines.get(active.session_id) if active else None
    if sm is not None:
        state = sm.state.value
    elif active is not None:
        state = active.state.value
    else:
        state = SessionState.IDLE.value
    return JSONResponse(
        {
            "active_session": active.session_id if active else None,
            "state": state,
            "current_persona": active.current_persona if active else None,
            "active_sessions_tracked": len(request.app.state.state_machines),
            # Warmup (model ısıtma) tamamlandı mı — ekran 'Hazırlanıyor…' overlay'i
            # bunu yoklar (True olunca seçim carousel'ini açar).
            "ready": bool(getattr(request.app.state, "ready", False)),
        }
    )


@router.get("/api/v1/characters")
async def characters() -> JSONResponse:
    return JSONResponse(
        [
            {"id": p.id, "name": p.name, "era": p.era, "expertise": p.expertise}
            for p in list_personas()
        ]
    )


@router.post("/api/v1/session/start")
async def session_start(request: Request) -> JSONResponse:
    store = _store(request)
    session = await store.create_async()
    sm = StateMachine(session.session_id, initial_state=session.state)
    request.app.state.state_machines[session.session_id] = sm

    async def broadcast(event: SessionEvent) -> None:
        for queue in _broadcast_listeners(request):
            # M-nice: yavaş panel transition'ı bloke etmesin — drop on full
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    sm.add_listener(broadcast)
    await sm.transition_to(SessionState.WELCOME)
    await broadcast(SessionEvent(session_id=session.session_id, type="session_started", data={}))
    sessions_total.labels(status="started").inc()
    return JSONResponse({"session_id": session.session_id, "state": sm.state.value})


@router.post("/api/v1/session/end")
async def session_end(request: Request) -> JSONResponse:
    store = _store(request)
    active = store.active
    if active is None:
        return JSONResponse({"detail": "No active session"}, status_code=404)
    sm = request.app.state.state_machines.get(active.session_id)
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
    await store.end_async(active.session_id)
    _drop_state_machine(request, active.session_id)  # M7
    sessions_total.labels(status="completed").inc()
    return JSONResponse({"session_id": active.session_id, "ended": True})


@router.post("/api/v1/emergency_stop")
async def emergency_stop(request: Request) -> JSONResponse:
    """M9: acil kes — çalışan turu iptal et + sahneyi karart + oturumu bitir."""
    app_state = request.app.state
    # 1) Çalışan pipeline turunu iptal et (cancel_event)
    cancel_ev = getattr(app_state, "active_cancel_event", None)
    if cancel_ev is not None:
        cancel_ev.set()
    # 2) Sahneye acil durdur + sustur
    scene = app_state.providers["scene"]
    await scene.send_command(
        SceneCommand(command="stop_audio", params={}, request_id=uuid.uuid4().hex)
    )
    await scene.send_command(
        SceneCommand(command="emergency_stop", params={}, request_id=uuid.uuid4().hex)
    )
    # 3) Oturumu bitir
    store = _store(request)
    active = store.active
    if active is not None:
        await store.end_async(active.session_id)
        _drop_state_machine(request, active.session_id)
    sessions_total.labels(status="error").inc()
    return JSONResponse({"status": "stopped"})


@router.post("/api/v1/interrupt")
async def interrupt(request: Request) -> JSONResponse:
    """M9: operatör 'sustur/atla' — çalışan turu iptal et ama oturumu kapatma."""
    app_state = request.app.state
    cancel_ev = getattr(app_state, "active_cancel_event", None)
    if cancel_ev is not None:
        cancel_ev.set()
    scene = app_state.providers.get("scene") if hasattr(app_state, "providers") else None
    if scene is not None:
        await scene.send_command(
            SceneCommand(command="stop_audio", params={}, request_id=uuid.uuid4().hex)
        )
    return JSONResponse({"status": "interrupted"})


@router.post("/api/v1/selection")
async def show_selection(request: Request) -> JSONResponse:
    """Operatör 'Karakter Seçimi'ne dön — çalışan turu kes, oturumu kapat,
    TEMİZ oturum aç ve sahneyi SELECTION (dönen carousel) durumuna al.

    Karakter kilidi (``current_persona``) sıfırlanır; ziyaretçi yeni bir âlim
    seçebilir. Ekran (ekran.html) ``state_changed`` → ``selection`` olayıyla
    seçim carousel'ini gösterir.
    """
    app_state = request.app.state
    # 1) Çalışan turu iptal et (sohbet ortasında basılırsa) + sesi sustur.
    cancel_ev = getattr(app_state, "active_cancel_event", None)
    if cancel_ev is not None:
        cancel_ev.set()
    scene = app_state.providers.get("scene") if hasattr(app_state, "providers") else None
    if scene is not None:
        await scene.send_command(
            SceneCommand(command="stop_audio", params={}, request_id=uuid.uuid4().hex)
        )
    # 2) Mevcut oturumu kapat.
    store = _store(request)
    active = store.active
    if active is not None:
        await store.end_async(active.session_id)
        _drop_state_machine(request, active.session_id)
    # 3) Temiz oturum (current_persona=None) + state machine → SELECTION.
    session = await store.create_async()
    sm = StateMachine(session.session_id, initial_state=session.state)
    request.app.state.state_machines[session.session_id] = sm

    async def broadcast(event: SessionEvent) -> None:
        for queue in _broadcast_listeners(request):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    sm.add_listener(broadcast)
    await sm.transition_to(SessionState.SELECTION)
    await broadcast(
        SessionEvent(session_id=session.session_id, type="session_started", data={})
    )
    sessions_total.labels(status="selection").inc()
    return JSONResponse({"session_id": session.session_id, "state": sm.state.value})


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
