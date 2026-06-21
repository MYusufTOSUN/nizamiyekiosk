"""BilimFest FESTİVAL KIOSK — tek process: mikrofon + sesli çıkış + web arayüzü.

Mikrofonu sürekli dinler, El-Cezerî sesli yanıt verir VE web arayüzünü
(``/ekran`` ziyaretçi hologram ekranı, ``/panel`` rehber paneli) canlı besler.
Tek model yükü (VRAM dostu) — sunucu + REPL ayrı çalıştırmaya gerek yok.

Akış: dinle → STT → Safety[giriş] → RAG → [hit=statik / miss=Claude] → Safety[çıkış]
      → TTS → hoparlör; her adımda /ws/events'e olay yayınlanır (ekran+panel).

Kullanım:
    python scripts/festival.py [--device 24] [--seconds 6] [--port 8000]
Ekran:  http://<pc-ip>:<port>/ekran     Panel: http://<pc-ip>:<port>/panel (PIN 1206)
Durdur: Ctrl+C
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.config import get_config
from src.core.factory import ProviderFactory
from src.core.interfaces import ConversationContext, DialogueTurn
from src.core.logger import configure_logging
from src.llm.llama_local import trim_to_last_sentence
from src.llm.persona import get_persona
from src.llm.rag_store import ChromaRAGStore
from src.llm.safety import SafetyFilter
from src.stt.audio_utils import numpy_to_pcm_bytes
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT
from src.tts.xtts_local import XTTS_NATIVE_SR, XTTSConfig, XTTSLocalTTS

_STATIC = Path(__file__).resolve().parents[1] / "static"
SR = 16000


def _ensure_api_key() -> None:
    """Windows: kalıcı User ortamından (registry) ANTHROPIC_API_KEY yükle."""
    import os

    if os.environ.get("ANTHROPIC_API_KEY") or sys.platform != "win32":
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            val, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
        if val:
            os.environ["ANTHROPIC_API_KEY"] = val
    except Exception:  # noqa: BLE001
        pass


# --- canlı olay yayını (ekran + panel) --------------------------------------
class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def send(self, type_: str, data: dict[str, Any]) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json({"type": type_, "data": data})
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


@dataclass
class Kiosk:
    state: str = "idle"
    session_id: str | None = None
    paused: bool = False
    persona_id: str = "cezeri"
    history: list[DialogueTurn] = field(default_factory=list)


# --- mikrofon + ses yardımcıları --------------------------------------------
def _record(device: Any, seconds: float) -> np.ndarray:
    a = sd.rec(int(seconds * SR), samplerate=SR, channels=1, device=device, dtype="float32")
    sd.wait()
    return a.flatten()


def _play(audio: np.ndarray) -> None:
    sd.play(audio, samplerate=XTTS_NATIVE_SR)
    sd.wait()


async def _transcribe(stt: WhisperLocalSTT, audio: np.ndarray) -> str:
    chunks = [
        numpy_to_pcm_bytes(audio[i : i + 320].astype(np.float32))
        for i in range(0, len(audio), 320)
    ]

    async def gen() -> Any:
        for c in chunks:
            yield c

    text = ""
    async for r in stt.transcribe_stream(gen()):
        text = r.text
        if r.is_final:
            break
    return text.strip()


# --- web uygulaması (ekran + panel + REST kontrol) --------------------------
def build_app(hub: Hub, kiosk: Kiosk) -> FastAPI:
    app = FastAPI(title="BilimFest Festival Kiosk")

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_STATIC / "index.html"))

    @app.get("/ekran")
    async def ekran() -> FileResponse:
        return FileResponse(str(_STATIC / "ekran.html"))

    @app.get("/panel")
    async def panel() -> FileResponse:
        return FileResponse(str(_STATIC / "panel.html"))

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/status")
    async def status() -> JSONResponse:
        return JSONResponse({
            "active_session": kiosk.session_id,
            "state": kiosk.state,
            "current_persona": kiosk.persona_id,
        })

    async def _set_state(s: str) -> None:
        kiosk.state = s
        await hub.send("state_changed", {"new": s})

    @app.post("/api/v1/session/start")
    async def session_start() -> JSONResponse:
        kiosk.history.clear()
        kiosk.session_id = uuid.uuid4().hex
        kiosk.paused = False
        await hub.send("session_started", {})
        await _set_state("welcome")
        return JSONResponse({"session_id": kiosk.session_id, "state": kiosk.state})

    @app.post("/api/v1/session/end")
    async def session_end() -> JSONResponse:
        kiosk.history.clear()
        kiosk.session_id = None
        kiosk.paused = True
        sd.stop()
        await _set_state("idle")
        return JSONResponse({"ended": True})

    @app.post("/api/v1/interrupt")
    async def interrupt() -> JSONResponse:
        sd.stop()  # mevcut konuşmayı kes/atla
        return JSONResponse({"status": "interrupted"})

    @app.post("/api/v1/emergency_stop")
    async def emergency_stop() -> JSONResponse:
        sd.stop()
        kiosk.history.clear()
        kiosk.session_id = None
        kiosk.paused = True
        await _set_state("idle")
        return JSONResponse({"status": "stopped"})

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.add(ws)
        # mevcut durumu hemen gönder (geç bağlanan ekran senkron olsun)
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "state_changed", "data": {"new": kiosk.state}})
        try:
            while True:
                await ws.receive_text()  # sadece disconnect'i bekle
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app


# --- konuşma döngüsü ---------------------------------------------------------
async def audio_loop(kiosk, hub, *, stt, tts, rag, llm, safety, persona, cfg, device, seconds):
    threshold = cfg.llm.rag.similarity_threshold
    margin = cfg.llm.rag.margin

    async def set_state(s: str) -> None:
        kiosk.state = s
        await hub.send("state_changed", {"new": s})

    print(f"\n[festival] Dinlemeye hazır. Ekran: /ekran  Panel: /panel  (cihaz={device})\n")
    while True:
        try:
            if kiosk.paused or kiosk.session_id is None:
                await asyncio.sleep(0.25)
                continue

            await set_state("listening")
            audio = await asyncio.to_thread(_record, device, seconds)
            if kiosk.paused or kiosk.session_id is None:
                continue
            text = await _transcribe(stt, audio)
            if not text:
                continue
            print(f"[ziyaretçi] {text}")
            await hub.send("transcription_received", {"text": text})

            category = safety.classify_input(text)
            if category is not None:
                response = persona.safety_fallbacks.get(category) or persona.safety_fallbacks.get(
                    "inappropriate", ""
                )
                source = "fallback"
            else:
                await set_state("thinking")
                results = await rag.query(text, persona.id, top_k=3)
                hit = (
                    results
                    and results[0].similarity >= threshold
                    and (len(results) < 2 or (results[0].similarity - results[1].similarity) >= margin)
                )
                if hit:
                    response = results[0].response_text
                    source = "rag"
                else:
                    ctx = ConversationContext(session_id=kiosk.session_id or "kiosk", persona_id=persona.id)
                    ctx.turns = list(kiosk.history[-6:])
                    ctx.retrieved = [r.response_text for r in results[:2]] if results else []
                    chunks: list[str] = []
                    try:
                        async for tok in llm.generate_response(text, persona, ctx):
                            chunks.append(tok)
                        response = trim_to_last_sentence("".join(chunks).strip())
                        verdict = safety.check_output(response, persona)
                        response = verdict.text
                    except Exception as exc:  # noqa: BLE001
                        print(f"[llm hata] {exc}")
                        response = persona.safety_fallbacks.get(
                            "unknown_modern", "Şu an cevap veremiyorum evladım."
                        )
                    source = "generated"

            print(f"[El-Cezerî/{source}] {response}")
            await hub.send("llm_response_completed", {"source": source, "text": response})
            kiosk.history.append(DialogueTurn(role="visitor", text=text))
            kiosk.history.append(DialogueTurn(role="persona", text=response))
            del kiosk.history[:-20]

            await set_state("speaking")
            pcm = bytearray()
            async for chunk in tts.synthesize_stream(response, persona.voice_id):
                pcm.extend(chunk)
            audio_out = np.frombuffer(bytes(pcm), dtype=np.int16)
            if audio_out.size and not kiosk.paused:
                await asyncio.to_thread(_play, audio_out)
            await set_state("listening")
        except Exception as exc:  # noqa: BLE001
            print(f"[döngü hata] {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.5)


async def _boot(cfg):
    persona = get_persona("cezeri")
    print("[1/4] TTS (XTTS) yükleniyor…")
    tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    await tts._ensure_model()
    async for _ in tts.synthesize_stream("Bir, iki, üç.", "cezeri"):
        pass
    print("[2/4] Whisper STT yükleniyor…")
    stt_kwargs = {**cfg.stt.config}
    stt_kwargs.setdefault("flush_on_stream_end", True)
    stt = WhisperLocalSTT(WhisperConfig(**stt_kwargs))
    await stt._ensure_model()
    print("[3/4] RAG (e5 + reranker) yükleniyor…")
    rag = ChromaRAGStore({
        "store_path": cfg.llm.rag.store_path,
        "candidate_pool": cfg.llm.rag.candidate_pool,
        "embedding_device": cfg.llm.rag.embedding_device,
        "use_reranker": cfg.llm.rag.use_reranker,
        "reranker_model": cfg.llm.rag.reranker_model,
    })
    await rag._ensure_ready()
    await rag.query("merhaba", "cezeri", top_k=1)
    print(f"[4/4] LLM ({cfg.llm.provider}) hazırlanıyor…")
    llm = ProviderFactory.create_llm(cfg.llm)
    if hasattr(llm, "warmup"):
        with contextlib.suppress(Exception):
            await llm.warmup(persona)
    return stt, tts, rag, llm, SafetyFilter(), persona


async def main(device: Any, seconds: float, port: int) -> int:
    configure_logging(level="WARNING")
    _ensure_api_key()
    cfg = get_config()
    print("\n=== BilimFest Festival Kiosk ===")
    stt, tts, rag, llm, safety, persona = await _boot(cfg)

    hub = Hub()
    kiosk = Kiosk(persona_id="cezeri", session_id=uuid.uuid4().hex)  # açılışta dinlemede
    app = build_app(hub, kiosk)
    # 0.0.0.0: ekran/panel festival LAN'ında başka cihazlardan açılır (kasıtlı)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")  # noqa: S104
    )

    loop = audio_loop(
        kiosk, hub, stt=stt, tts=tts, rag=rag, llm=llm, safety=safety,
        persona=persona, cfg=cfg, device=device, seconds=seconds,
    )
    await asyncio.gather(server.serve(), loop)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="BilimFest festival kiosk (mic + ses + web)")
    ap.add_argument("--device", default=None, help="mikrofon cihaz index'i (mic_level_check ile bul)")
    ap.add_argument("--seconds", type=float, default=6.0, help="dinleme penceresi (sn)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    dev: Any = args.device
    if dev is not None and str(dev).isdigit():
        dev = int(dev)
    try:
        raise SystemExit(asyncio.run(main(dev, args.seconds, args.port)))
    except KeyboardInterrupt:
        print("\n[festival] kapatıldı.")
