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
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core.config import AppConfig, get_config
from src.core.errors import ConfigError
from src.core.factory import ProviderFactory, validate_startup
from src.core.interfaces import (
    SceneCommand,
    SceneController,
    SessionEvent,
    SessionState,
)
from src.core.logger import configure_logging, get_logger
from src.llm.persona import list_personas
from src.orchestrator.monitor import gpu_vram_monitor
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

    # M5: fail-fast — eksik model/dosya production'da açılmayı engeller
    problems = validate_startup(config)
    if problems:
        if config.app.environment == "production":
            raise ConfigError(
                "CFG_001",
                "Üretim başlatma doğrulaması başarısız: " + "; ".join(problems),
            )
        _log.warning("startup_validation_warnings", problems=problems)

    providers = ProviderFactory.build_all(config)
    app.state.config = config
    app.state.providers = providers
    app.state.sessions = SessionStore(
        max_duration_seconds=config.session.max_duration_seconds,
    )
    app.state.state_machines = {}
    event_listeners: list[asyncio.Queue[SessionEvent]] = []
    app.state.event_listeners = event_listeners
    app.state.active_cancel_event = None  # M9: çalışan turun iptal bayrağı
    app.state.pipeline = ConversationPipeline(
        stt=providers["stt"],  # type: ignore[arg-type]
        intent=providers["intent"],  # type: ignore[arg-type]
        llm=providers["llm"],  # type: ignore[arg-type]
        rag=providers["rag"],  # type: ignore[arg-type]
        tts=providers["tts"],  # type: ignore[arg-type]
        lipsync=providers["lipsync"],  # type: ignore[arg-type]
        scene=providers["scene"],  # type: ignore[arg-type]
        rag_similarity_threshold=config.llm.rag.similarity_threshold,
        rag_margin=config.llm.rag.margin,
    )

    # Scene controller'a init komutu
    scene_ctl = cast(SceneController, providers["scene"])
    await scene_ctl.send_command(
        SceneCommand(
            command="init",
            params={"environment": config.app.environment},
            request_id=uuid.uuid4().hex,
        )
    )

    # === NON-BLOCKING WARMUP (best-practice cold-start çözümü) ===
    # ESKİDEN: warmup 'yield' öncesi sıralı çalışıyordu → sunucu tüm modeller
    # yüklenene kadar HİÇBİR isteği (statik ekran.html dahil) kabul etmiyordu
    # ("proje ilk açılınca çok bekliyor"). ARTIK: warmup ARKA PLANDA; sunucu
    # anında açılır, ekran hemen gelir ve hazır olana dek 'Hazırlanıyor…' gösterir.
    # Ayrıca TÜM aktif karakterler ısıtılır (yalnız cezeri değil) → hangi âlim
    # seçilirse seçilsin ilk cevap soğuk-start yaşamaz ("seçince bekletiyor" çözümü).
    app.state.ready = False

    async def _warmup_all() -> None:
        import time as _t

        t0 = _t.perf_counter()
        tts = providers["tts"]
        stt = providers["stt"]
        llm = providers["llm"]
        rag = providers["rag"]
        personas = list_personas()
        # cuDNN SIRASI KRİTİK: XTTS modeli Whisper'DAN ÖNCE yüklenmeli (torch cuDNN
        # context'i XTTS kursun; aksi halde XTTS CPU'ya düşüp ~10x yavaşlar).
        first_voice = personas[0].voice_id if personas else "cezeri"
        try:
            if hasattr(tts, "warmup"):
                try:
                    ms = await tts.warmup(first_voice)  # type: ignore[attr-defined]
                    _log.info("tts_warmup_done", voice=first_voice, ms=ms)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("tts_warmup_failed", error=str(exc))
            if hasattr(stt, "warmup"):
                try:
                    ms = await stt.warmup()  # type: ignore[attr-defined]
                    _log.info("stt_warmup_done", ms=ms)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("stt_warmup_failed", error=str(exc))
            # HER aktif karakter için: RAG koleksiyonu + LLM prompt-cache + TTS latent.
            # (RAG e5/reranker ilk çağrıda shared yüklenir; sonraki personalar yalnız
            # kendi Chroma koleksiyonunu ısıtır. XTTS ilk voice'u yüklü; kalanlar yalnız
            # speaker-latent hesaplar.)
            for p in personas:
                if hasattr(rag, "warmup"):
                    try:
                        await rag.warmup(p.id)  # type: ignore[attr-defined]
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("rag_warmup_failed", persona=p.id, error=str(exc))
                if hasattr(llm, "warmup"):
                    try:
                        await llm.warmup(p)  # type: ignore[attr-defined]
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("llm_warmup_failed", persona=p.id, error=str(exc))
                if hasattr(tts, "warmup"):
                    try:
                        await tts.warmup(p.voice_id)  # type: ignore[attr-defined]
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("tts_warmup_failed", persona=p.id, error=str(exc))
        finally:
            # Kısmi hata olsa bile kapı sonsuza kilitlenmesin: her durumda hazır işaretle.
            app.state.ready = True
            _log.info(
                "warmup_all_done",
                personas=len(personas),
                ms=int((_t.perf_counter() - t0) * 1000),
            )
            # Ekran/panel 'Hazırlanıyor…' overlay'ini kaldırsın diye hazır sinyali.
            ready_ev = SessionEvent(session_id="system", type="system_ready", data={})
            for q in event_listeners:
                try:
                    q.put_nowait(ready_ev)
                except asyncio.QueueFull:
                    pass

    warmup_task = asyncio.create_task(_warmup_all())

    # Arka plan görevleri: watchdog + VRAM monitor (M3)
    watchdog_task = asyncio.create_task(
        session_watchdog(
            sessions=app.state.sessions,
            state_machines=app.state.state_machines,
        )
    )
    vram_task = asyncio.create_task(gpu_vram_monitor())

    yield

    _log.info("shutdown")
    for t in (warmup_task, watchdog_task, vram_task):
        t.cancel()
        try:
            await t
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

# --- Web arayüzü: ziyaretçi ekranı (/ekran) + rehber paneli (/panel) ---------
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def _index() -> FileResponse:
        return FileResponse(str(_STATIC_DIR / "index.html"))

    @app.get("/ekran")
    async def _ekran() -> FileResponse:
        """Ziyaretçinin gördüğü hologram ekranı (salt-okunur)."""
        return FileResponse(str(_STATIC_DIR / "ekran.html"))

    @app.get("/panel")
    async def _panel() -> FileResponse:
        """Rehber/operatör paneli (PIN korumalı yönetim)."""
        return FileResponse(str(_STATIC_DIR / "panel.html"))


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket) -> None:
    """Ziyaretçi mikrofonundan PCM stream alır, pipeline'ı çalıştırır."""
    await websocket.accept()
    app_state: Any = websocket.app.state

    # WARMUP KAPISI: modeller (XTTS→Whisper DOĞRU SIRADA) yüklenene kadar bekle.
    # Aksi halde ilk tur run_turn'ün lazy-load'u arka plan warmup'ıyla yarışıp
    # XTTS'i CPU'ya düşürebilir (kalıcı ~10x yavaşlama). Kurulum ilk ~1 dk sürer;
    # ziyaretçi o an etkileşime girmez. 60 sn sonra yine de devam et (güvenlik).
    waited = 0.0
    while not getattr(app_state, "ready", True) and waited < 60.0:
        await asyncio.sleep(0.2)
        waited += 0.2

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
    cancel_event = asyncio.Event()
    app_state.active_cancel_event = cancel_event  # M9: /interrupt + /emergency_stop bunu set eder
    try:
        result = await app_state.pipeline.run_turn(
            session=session,
            sm=sm,
            audio_stream=audio_iter(),
            cancel_event=cancel_event,
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
        if app_state.active_cancel_event is cancel_event:
            app_state.active_cancel_event = None
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
