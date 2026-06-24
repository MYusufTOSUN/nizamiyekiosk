"""BilimFest FESTİVAL KIOSK — tek process: mikrofon + sesli çıkış + web arayüzü.

Mikrofonu sürekli dinler, El-Cezerî sesli yanıt verir VE web arayüzünü
(``/ekran`` ziyaretçi hologram ekranı, ``/panel`` rehber paneli) canlı besler.
Tek model yükü (VRAM dostu).

Doğal sıra-alma:
- Sessizlik-tabanlı dinleme: ziyaretçi düşünüp DURAKLAYABİLİR; sistem ancak
  ``--silence`` kadar sürekli sessizlikten sonra cevaba geçer (hemen kesmez).
- Barge-in: El-Cezerî konuşurken ziyaretçi konuşmaya başlarsa cevap KESİLİR ve
  sistem dinlemeye döner (``--no-barge`` ile kapatılır).

Akış: dinle → STT → Safety[giriş] → RAG → [hit=statik / miss=Claude]
      → Safety[çıkış] → TTS → hoparlör; her adımda /ws/events yayınlanır.

Kullanım:
    python scripts/festival.py [--device 24] [--silence 1.5] [--threshold 0.02]
                               [--max-listen 12] [--no-barge] [--port 8000]
Ekran:  http://<pc-ip>:<port>/ekran     Panel: http://<pc-ip>:<port>/panel (PIN 1206)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import queue
import sys
import time
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
from src.tts.xtts_local import XTTS_NATIVE_SR

_STATIC = Path(__file__).resolve().parents[1] / "static"
SR = 16000
FRAME = 480  # 30 ms @ 16 kHz


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


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame**2))) if frame.size else 0.0


def _prep_audio(audio: np.ndarray | None, floor: float) -> np.ndarray | None:
    """Whisper'a vermeden önce: gerçek konuşma yoksa (peak<floor) AT — sessizlik/
    gürültü Whisper'a gitmesin (halüsinasyon önler). Varsa seviyeyi normalize et:
    kısık mikrofonda bile Whisper net/yüksek ses duyar, doğru transkript çıkar."""
    if audio is None or audio.size == 0:
        return None
    peak = float(np.max(np.abs(audio)))
    if peak < floor:
        return None
    return (audio / peak * 0.6).astype(np.float32)


# --- ayar paketi -------------------------------------------------------------
@dataclass
class Opts:
    threshold: float = 0.012      # konuşma VAD eşiği — düşük: kısık mikrofonu da yakalar
    min_voice: float = 0.02       # yakalanan klip bu peak'in altındaysa GÜRÜLTÜ → at (halüsinasyon önler)
    silence_ms: int = 1500        # bu kadar sürekli sessizlik = konuşma bitti (duraklama payı)
    onset_ms: int = 150           # konuşma başladı saymak için min süre
    max_listen_ms: int = 12000    # tek söyleyiş üst sınırı
    barge: bool = True            # konuşurken araya girilince cevabı kes
    barge_threshold: float = 0.03  # barge TABAN eşiği (mutlak)
    barge_ratio: float = 2.0      # ziyaretçi, El-Cezerî eko tabanının bu katı üstüne çıkarsa barge
    barge_ms: int = 350           # barge için gereken sürekli konuşma süresi (ms)


# --- kalıcı mikrofon akışı ---------------------------------------------------
class Mic:
    def __init__(self, device: Any) -> None:
        self.device = device
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any = None

    def start(self) -> None:
        def cb(indata: Any, _f: int, _t: Any, _s: Any) -> None:
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self.q.put(np.asarray(mono, dtype=np.float32).copy())

        self._stream = sd.InputStream(
            samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
            device=self.device, callback=cb,
        )
        self._stream.start()

    def drain(self) -> None:
        with contextlib.suppress(queue.Empty):
            while True:
                self.q.get_nowait()

    def stop(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()


def _listen_blocking(mic: Mic, opts: Opts, is_active) -> np.ndarray | None:
    """Konuşma başlangıcını bekle, DURAKLAMALARA tolerans göstererek söyleyişi
    sürekli sessizliğe kadar topla. is_active() False olursa iptal eder."""
    mic.drain()
    fr_ms = FRAME / SR * 1000.0
    onset_need = max(1, int(opts.onset_ms / fr_ms))
    sil_need = max(1, int(opts.silence_ms / fr_ms))
    max_frames = int(opts.max_listen_ms / fr_ms)
    frames: list[np.ndarray] = []
    started = False
    speech_run = 0
    silence_run = 0
    count = 0
    while count < max_frames:
        if not is_active():
            return None
        try:
            f = mic.q.get(timeout=0.2)
        except queue.Empty:
            continue
        count += 1
        loud = _rms(f) > opts.threshold
        if not started:
            if loud:
                speech_run += 1
                if speech_run >= onset_need:
                    started = True
                    frames.append(f)
            else:
                speech_run = 0
            continue
        frames.append(f)
        if loud:
            silence_run = 0
        else:
            silence_run += 1
            if silence_run >= sil_need:
                break
    if not started or not frames:
        return None
    return np.concatenate(frames)


def _speak_blocking(mic: Mic, audio: np.ndarray, opts: Opts) -> bool:
    """TTS sesini çal; barge açıksa, ziyaretçi El-Cezerî'nin ÜSTÜNE konuşunca KES.
    Hoparlörden mikrofona sızan El-Cezerî sesini (eko) playback başında ölçer,
    eşiği ona göre ADAPTİF ayarlar; ziyaretçi eko tabanının belirgin üstüne çıkıp
    SÜRERSE durur (True). En sağlamı: yönlü/yakın mik + ayrı hoparlör."""
    mic.drain()
    fr_ms = FRAME / SR * 1000.0
    start = time.monotonic()
    dur = audio.size / XTTS_NATIVE_SR
    sd.play(audio, samplerate=XTTS_NATIVE_SR)
    if not opts.barge:
        sd.wait()
        return False
    # 1) Eko tabanı: ilk ~0.35 sn yalnız El-Cezerî çalar (ziyaretçi henüz konuşmadı)
    base: list[float] = []
    while (time.monotonic() - start) < 0.35 and (time.monotonic() - start) < dur:
        try:
            base.append(_rms(mic.q.get(timeout=0.1)))
        except queue.Empty:
            pass
    echo = sorted(base)[len(base) // 2] if base else 0.0  # medyan eko seviyesi
    trig = max(opts.barge_threshold, echo * opts.barge_ratio)
    # 2) Kalan playback: ziyaretçi eşiği geçip SÜRERSE kes
    need = max(1, int(opts.barge_ms / fr_ms))
    speech = 0
    while (time.monotonic() - start) < dur + 0.15:
        try:
            f = mic.q.get(timeout=0.1)
        except queue.Empty:
            continue
        if _rms(f) > trig:
            speech += 1
            if speech >= need:
                sd.stop()
                return True
        else:
            speech = 0
    sd.wait()
    return False


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
        sd.stop()
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
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "state_changed", "data": {"new": kiosk.state}})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app


# --- konuşma döngüsü ---------------------------------------------------------
async def audio_loop(kiosk, hub, *, stt, tts, rag, llm, safety, persona, cfg, mic, opts):
    threshold = cfg.llm.rag.similarity_threshold
    margin = cfg.llm.rag.margin

    async def set_state(s: str) -> None:
        kiosk.state = s
        await hub.send("state_changed", {"new": s})

    def active() -> bool:
        return not kiosk.paused and kiosk.session_id is not None

    print("\n[festival] Dinlemeye hazır. Ekran: /ekran  Panel: /panel\n")
    while True:
        try:
            if not active():
                await asyncio.sleep(0.2)
                continue

            await set_state("listening")
            audio = await asyncio.to_thread(_listen_blocking, mic, opts, active)
            audio = _prep_audio(audio, opts.min_voice)  # gürültü-eşiği + normalize
            if audio is None or not active():
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
                        response = safety.check_output(response, persona).text
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
            if audio_out.size and active():
                barged = await asyncio.to_thread(_speak_blocking, mic, audio_out, opts)
                if barged:
                    print("[barge-in] ziyaretçi araya girdi — dinlemeye dönülüyor")
            await set_state("listening")
        except Exception as exc:  # noqa: BLE001
            print(f"[döngü hata] {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.5)


async def _boot(cfg):
    persona = get_persona("cezeri")
    print(f"[1/4] TTS ({cfg.tts.provider}) yükleniyor…")
    tts = ProviderFactory.create_tts(cfg.tts)
    async for _ in tts.synthesize_stream("Bir, iki, üç.", "cezeri"):  # ısıt (XTTS: modeli yükler)
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


async def main(device: Any, opts: Opts, port: int) -> int:
    configure_logging(level="WARNING")
    _ensure_api_key()
    cfg = get_config()
    print("\n=== BilimFest Festival Kiosk ===")
    stt, tts, rag, llm, safety, persona = await _boot(cfg)

    mic = Mic(device)
    mic.start()
    hub = Hub()
    kiosk = Kiosk(persona_id="cezeri", session_id=uuid.uuid4().hex)
    app = build_app(hub, kiosk)
    # 0.0.0.0: ekran/panel festival LAN'ında başka cihazlardan açılır (kasıtlı)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")  # noqa: S104
    )
    loop = audio_loop(
        kiosk, hub, stt=stt, tts=tts, rag=rag, llm=llm, safety=safety,
        persona=persona, cfg=cfg, mic=mic, opts=opts,
    )
    try:
        await asyncio.gather(server.serve(), loop)
    finally:
        mic.stop()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="BilimFest festival kiosk (mic + ses + web)")
    ap.add_argument("--device", default=None, help="mikrofon cihaz index'i (mic_level_check ile bul)")
    ap.add_argument("--threshold", type=float, default=0.012, help="konuşma VAD eşiği (kısık mik için düşür)")
    ap.add_argument("--min-voice", type=float, default=0.02, help="bu peak altındaki klip gürültü sayılır, atılır")
    ap.add_argument("--silence", type=float, default=1.5, help="duraklama payı: bu kadar sn sessizlik = bitti")
    ap.add_argument("--max-listen", type=float, default=12.0, help="tek söyleyiş üst sınırı (sn)")
    ap.add_argument("--no-barge", action="store_true", help="konuşurken araya girince kesmeyi KAPAT")
    ap.add_argument("--barge-threshold", type=float, default=0.06, help="barge-in eşiği (eko için yüksek)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    dev: Any = args.device
    if dev is not None and str(dev).isdigit():
        dev = int(dev)
    opts = Opts(
        threshold=args.threshold,
        min_voice=args.min_voice,
        silence_ms=int(args.silence * 1000),
        max_listen_ms=int(args.max_listen * 1000),
        barge=not args.no_barge,
        barge_threshold=args.barge_threshold,
    )
    try:
        raise SystemExit(asyncio.run(main(dev, opts, args.port)))
    except KeyboardInterrupt:
        print("\n[festival] kapatıldı.")
