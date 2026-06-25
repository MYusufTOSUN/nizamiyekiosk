"""BilimFest FESTİVAL KIOSK — tek process: mikrofon + sesli çıkış + web arayüzü.

Ses çekirdeği ``src/audio/engine.py``'ye taşındı (sıfırdan, kendini kalibre eden):
- Mikrofon NATIVE hızda yakalanır (Realtek 44.1 kHz), 16 kHz'e anti-aliasing'li
  indirilir (MME'nin kör resample'ından kaynaklı seviye/kalite sorunu biter).
- AÇILIŞTA OTOMATİK KALİBRASYON: boştayken ortam gürültü tabanını ölçer, tüm
  eşikleri buna göre TÜRETİR; kısa bir El-Cezerî sesiyle hoparlör->mik eko'sunu +
  oda yankısını ölçüp barge referans-kapısını ayarlar.
- BARGE-IN REFERANS-KAPILI: El-Cezerî'nin gerçek TTS çıkışını referans alır; o
  konuşurken detektör kapalı, yalnız sustuğunda mikrofon yüksekse keser. Kendi
  sesi barge'ı ASLA tetikleyemez. CLI ile her eşik EZİLEBİLİR.

28-saatlik sergi sağlamlığı (korunur): aşama-başı timeout, VRAM/OOM izleme,
mikrofon watchdog, fail-fast (validate_startup), sunucu-tarafı operatör token'ı.

Akış: dinle -> STT -> Safety[giriş] -> RAG -> [hit=statik / miss=Claude]
      -> Safety[çıkış] -> TTS(akış+barge) -> hoparlör; her adımda /ws/events yayınlanır.

Kullanım:
    python scripts/festival.py [--device 1] [--silence 2.0] [--max-listen 15]
        [--no-barge] [--no-calibrate] [--barge-threshold X] [--barge-ref-quiet X]
        [--barge-echo-guard MS] [--barge-ms MS] [--min-voice X] [--threshold X] [--port 8000]
Teşhis/kalibrasyon:  python scripts/audio_check.py --device 1
Ekran: /ekran   Panel: /panel (PIN 1206; BFEST_OP_TOKEN ile değişir)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.audio import engine as E
from src.core.config import get_config
from src.core.factory import ProviderFactory, validate_startup
from src.core.interfaces import ConversationContext, DialogueTurn
from src.core.logger import configure_logging
from src.llm.llama_local import trim_to_last_sentence
from src.llm.persona import get_persona
from src.llm.rag_store import ChromaRAGStore
from src.llm.safety import SafetyFilter
from src.stt.audio_utils import numpy_to_pcm_bytes
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT

_STATIC = Path(__file__).resolve().parents[1] / "static"

# --- aşama-başı timeout bütçeleri (sn) — asılı çağrı kiosk'u dondurmasın --------
STT_TIMEOUT = 10.0
RAG_TIMEOUT = 5.0
LLM_TIMEOUT = 25.0
TTS_FIRST_CHUNK_TIMEOUT = 20.0
TTS_CHUNK_TIMEOUT = 10.0
# --- operasyon sabitleri -------------------------------------------------------
VRAM_LOG_INTERVAL = 60.0
MIC_STALE_SECONDS = 3.0
IDLE_RESET_SECONDS = 90.0
GROUNDING_MIN = 0.45  # LLM grounding'ine yalnız bu skorun üstündeki RAG adayı verilir
# OFFLINE/internet-yok modu: LLM çağrılamayınca lokal RAG'i DAHA CÖMERT eşikle kullan
# (online 0.66 yerine) — genel fallback yerine gerçek kurate bilgi versin, "idare etsin".
OFFLINE_FALLBACK_THRESHOLD = 0.58
# RAG-hit bile yoksa: konuyu KAPSANAN alanlara çeken DEĞİŞKEN nazik yönlendirmeler.
OFFLINE_DEFLECTIONS = [
    "Bunu tam çıkaramadım evladım, kulağım asırların ardından geliyor. Sen bana "
    "atölyemden sor — fil saatini mi, tavus kuşunu mu merak edersin?",
    "Şu an bunu iyi işitemedim evladım. Gel sana bildiğim bir şeyi anlatayım: su "
    "saatlerimi mi, dişlilerimi mi, yoksa şifreli kilitlerimi mi?",
    "Bu sorunu net alamadım evladım. Ama makinelerimden, kitabımdan, Cizre'den "
    "konuşabiliriz — hangisini istersin?",
    "Affet evladım, bunu duyamadım. Sen bana mucitliğimi, su pompalarımı ya da "
    "müzik otomatlarımı sorsana?",
]


def _ensure_api_key() -> None:
    """Windows: kalıcı User ortamından (registry) ANTHROPIC_API_KEY yükle."""
    if os.environ.get("ANTHROPIC_API_KEY") or sys.platform != "win32":
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            val, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
        if val:
            os.environ["ANTHROPIC_API_KEY"] = val
    except FileNotFoundError:
        print("[uyarı] registry'de ANTHROPIC_API_KEY yok (HKCU\\Environment).")
    except OSError as exc:
        print(f"[uyarı] registry'den ANTHROPIC_API_KEY okunamadı: {exc}")


# --- GPU yardımcıları (VRAM/OOM dayanıklılığı) --------------------------------
def _empty_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _vram_str() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            alloc = torch.cuda.memory_allocated() / 1e9
            reserved = torch.cuda.memory_reserved() / 1e9
            return f"alloc={alloc:.2f}GB reserved={reserved:.2f}GB"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _is_oom(exc: BaseException) -> bool:
    return "out of memory" in str(exc).lower()


# Sahne yönergesi / jest betimlemesi temizliği (her şey HOPARLÖRDEN okunur).
_EMOTE_WRAP = re.compile(r"[*\[(][^*\])]{0,80}?[*\])]")
_NARR_ADVERB = (
    "nazikçe", "hafifçe", "usulca", "yavaşça", "sakince", "içtenlikle", "gülerek",
    "gülümseyerek", "düşünerek", "eğilerek", "tebessümle", "şefkatle", "merakla",
)
_NARR_VERB = (
    "gülümser", "gülümsüyor", "düşünür", "düşünüyor", "eğilir", "bakar", "bakıyor",
    "başını sallar", "nefes alır", "iç çeker", "göz kırpar",
)


def _strip_stage_directions(text: str) -> str:
    """LLM çıktısından sahne yönergesi/jest betimlemesini at — yoksa TTS 'Nazikçe
    gülümser...' diye OKUR. (1) *...*/(...)/[...] sarmaları, (2) baştaki anlatı
    cümlesi (…/—/... ile biten ve anlatı sözcüğü içeren) kaldırılır."""
    t = _EMOTE_WRAP.sub(" ", text)
    m = re.match(r"^\s*([^.!?…]{1,60}?)\s*(\.\.\.|…|—)\s+(.+)$", t, re.S)
    if m:
        lead = m.group(1).lower()
        if lead.startswith(_NARR_ADVERB) or any(v in lead for v in _NARR_VERB):
            t = m.group(3)
    return re.sub(r"\s+", " ", t).strip()


def _safe_console() -> None:
    """Konsol cp1254 (Türkçe) olabilir; kodlanamayan sembol CRASH etmesin diye
    stdout/stderr'i errors='replace' yap. (baslat.py PYTHONUTF8=1 verir; doğrudan
    çalıştırmada bu güvenlik ağı devreye girer.)"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]


# --- ayar paketi (CLI -> engine config + kalibrasyon override'ları) -----------
@dataclass
class Opts:
    device: Any = None
    port: int = 8000
    barge: bool = False           # ŞİMDİLİK KAPALI (kod duruyor; --barge ile açılır)
    do_calibrate: bool = True
    ambient_seconds: float = 3.0
    min_confidence: float = 0.0
    endpoint: E.EndpointConfig = field(default_factory=E.EndpointConfig)
    barge_cfg: E.BargeConfig = field(default_factory=E.BargeConfig)
    overrides: dict[str, float] = field(default_factory=dict)  # kalibrasyonu ezen değerler


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
    llm_ok: bool = True
    mic_ok: bool = True
    last_activity: float = field(default_factory=time.monotonic)


# --- web uygulaması (ekran + panel + REST kontrol) --------------------------
def build_app(hub: Hub, kiosk: Kiosk, op_token: str) -> FastAPI:
    app = FastAPI(title="BilimFest Festival Kiosk")

    def require_op(x_op_token: str = Header(default="")) -> None:
        """Mutating kontrol uçları için sunucu-tarafı yetki (PIN yalnız UX değil)."""
        if x_op_token != op_token:
            raise HTTPException(status_code=401, detail="yetkisiz (geçersiz operatör token'ı)")

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    # HTML'i CACHE'LEME: tarayıcı eski panel.html'i (token göndermeyen) tutmasın.
    _nocache = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_STATIC / "index.html"), headers=_nocache)

    @app.get("/ekran")
    async def ekran() -> FileResponse:
        return FileResponse(str(_STATIC / "ekran.html"), headers=_nocache)

    @app.get("/panel")
    async def panel() -> FileResponse:
        return FileResponse(str(_STATIC / "panel.html"), headers=_nocache)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/op/verify", dependencies=[Depends(require_op)])
    async def op_verify() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/v1/status")
    async def status() -> JSONResponse:
        return JSONResponse({
            "active_session": kiosk.session_id,
            "state": kiosk.state,
            "current_persona": kiosk.persona_id,
            "llm_ok": kiosk.llm_ok,
            "mic_ok": kiosk.mic_ok,
        })

    async def _set_state(s: str) -> None:
        kiosk.state = s
        await hub.send("state_changed", {"new": s})

    @app.post("/api/v1/session/start", dependencies=[Depends(require_op)])
    async def session_start() -> JSONResponse:
        kiosk.history.clear()
        kiosk.session_id = uuid.uuid4().hex
        kiosk.paused = False
        kiosk.last_activity = time.monotonic()
        await hub.send("session_started", {})
        await _set_state("welcome")
        return JSONResponse({"session_id": kiosk.session_id, "state": kiosk.state})

    @app.post("/api/v1/session/end", dependencies=[Depends(require_op)])
    async def session_end() -> JSONResponse:
        kiosk.history.clear()
        kiosk.session_id = None
        kiosk.paused = True
        sd.stop()
        await _set_state("idle")
        return JSONResponse({"ended": True})

    @app.post("/api/v1/interrupt", dependencies=[Depends(require_op)])
    async def interrupt() -> JSONResponse:
        sd.stop()
        return JSONResponse({"status": "interrupted"})

    @app.post("/api/v1/emergency_stop", dependencies=[Depends(require_op)])
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


# --- arka plan görevleri: VRAM izleme + mikrofon watchdog --------------------
async def vram_monitor() -> None:
    while True:
        await asyncio.sleep(VRAM_LOG_INTERVAL)
        s = _vram_str()
        if s:
            print(f"[vram] {s}")


async def mic_watchdog(mic: E.MicStream, hub: Hub, kiosk: Kiosk) -> None:
    """Mikrofon akışı ölürse (USB/driver hiccup) re-open dener, panele bildirir."""
    while True:
        await asyncio.sleep(1.0)
        if mic.stale_for() <= MIC_STALE_SECONDS:
            if not kiosk.mic_ok:
                kiosk.mic_ok = True
                await hub.send("mic_ok", {})
            continue
        print(f"[MİK UYARI] {mic.stale_for():.1f} sn frame yok — mikrofon yeniden açılıyor")
        kiosk.mic_ok = False
        await hub.send("mic_lost", {"stale_s": round(mic.stale_for(), 1)})
        try:
            await asyncio.to_thread(mic.restart)
        except Exception as exc:  # noqa: BLE001
            print(f"[MİK HATA] yeniden açılamadı: {exc}")


async def _transcribe(stt: WhisperLocalSTT, audio16: np.ndarray) -> tuple[str, float]:
    """16 kHz float32 söyleyişi Whisper'a ver (motor zaten resample+normalize etti)."""
    chunks = [
        numpy_to_pcm_bytes(audio16[i : i + 320].astype(np.float32))
        for i in range(0, len(audio16), 320)
    ]

    async def gen() -> Any:
        for c in chunks:
            yield c

    text = ""
    confidence = 0.0
    async for r in stt.transcribe_stream(gen()):
        text = r.text
        confidence = r.confidence
        if r.is_final:
            break
    return text.strip(), confidence


# --- konuşma döngüsü ---------------------------------------------------------
async def audio_loop(kiosk, hub, *, stt, tts, rag, llm, safety, persona, cfg, mic,
                     endpointer: E.Endpointer, barge: E.BargeDetector, opts: Opts):
    margin = cfg.llm.rag.margin  # offline-yedek hit margin'i
    cooldown = barge.cfg.cooldown_ms / 1000.0

    async def set_state(s: str) -> None:
        kiosk.state = s
        await hub.send("state_changed", {"new": s})

    def active() -> bool:
        return not kiosk.paused and kiosk.session_id is not None

    async def _collect_tts(text: str) -> np.ndarray:
        """TTS'i tamamen topla (per-chunk timeout ile — asılma koruması).

        sd.play tüm tamponu ister; akış-çalma (sd.OutputStream) MME'de kilitlendiği
        için topla-sonra-çal yapıyoruz (eski kanıtlı yöntem). Kısa persona
        cevaplarında gecikme ihmal edilebilir; cache-hit zaten anında."""
        agen = tts.synthesize_stream(text, persona.voice_id)
        pcm = bytearray()
        first = True
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        agen.__anext__(),
                        TTS_FIRST_CHUNK_TIMEOUT if first else TTS_CHUNK_TIMEOUT,
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    print("[tts timeout] sentez asıldı — eldeki kısım çalınıyor")
                    break
                first = False
                pcm.extend(chunk)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(agen.aclose(), 3.0)
        return np.frombuffer(bytes(pcm), dtype=np.int16)

    async def synth_and_play(text: str) -> bool:
        """TTS'i topla + sd.play ile çal.

        Barge KAPALI (varsayılan): sd.play + sd.wait — KANITLI, TAM bitene kadar
        çalar (duvar-saati poll + koşulsuz sd.stop YOK → cümle ortada kesilmez).
        Barge AÇIK: playback boyu referans-kapılı izle, tetiklenirse kes."""
        audio = await _collect_tts(text)
        secs = audio.size / E.SPEAKER_SR
        print(f"[tts] toplandı {secs:.2f}s ({audio.size} örnek)")
        if audio.size == 0 or not active():
            return False
        spk = E.SpeakerStream()
        if not opts.barge:
            try:
                await asyncio.to_thread(spk.play_array, audio)
                await asyncio.to_thread(spk.wait_done)   # sd.wait — gerçek bitiş
            finally:
                spk.close()
            return False
        # --- Barge AÇIK ---
        stop_evt = threading.Event()
        barge_evt = threading.Event()
        mic.drain()
        monitor = threading.Thread(
            target=barge.monitor, args=(mic, spk, stop_evt, barge_evt), daemon=True
        )
        monitor.start()
        try:
            await asyncio.to_thread(spk.play_array, audio)
            t0 = time.monotonic()
            while time.monotonic() - t0 < secs + 0.3:
                if barge_evt.is_set():
                    break
                await asyncio.sleep(0.03)
            if barge_evt.is_set():
                await asyncio.to_thread(spk.stop)
            else:
                await asyncio.to_thread(spk.wait_done)   # bitişi garanti et
        finally:
            stop_evt.set()
            await asyncio.to_thread(spk.close)
            monitor.join(timeout=1.0)
        return barge_evt.is_set()

    print("\n[festival] Dinlemeye hazır. Ekran: /ekran  Panel: /panel\n")
    while True:
        try:
            if not active():
                await asyncio.sleep(0.2)
                continue

            if kiosk.history and (time.monotonic() - kiosk.last_activity) > IDLE_RESET_SECONDS:
                kiosk.history.clear()
                await hub.send("idle_reset", {})
                print("[idle] uzun sessizlik — sohbet hafızası sıfırlandı")

            await set_state("listening")
            audio16, reason = await asyncio.to_thread(endpointer.listen, mic, active)
            if audio16 is None:
                if reason == "çok_kısık":
                    await hub.send("audio_too_quiet", {})
                continue

            t_stt0 = time.monotonic()
            try:
                text, confidence = await asyncio.wait_for(
                    _transcribe(stt, audio16), STT_TIMEOUT
                )
            except TimeoutError:
                print("[stt timeout] transkripsiyon asıldı — atlanıyor")
                continue
            print(f"[zaman] stt={time.monotonic() - t_stt0:.2f}s")
            if not text or len(text) < 2:
                continue
            if opts.min_confidence > 0 and confidence < opts.min_confidence:
                print(f"[düşük güven] {confidence:.2f} < {opts.min_confidence} — atlanıyor")
                continue
            print(f"[ziyaretçi] {text}  (conf={confidence:.2f})")
            kiosk.last_activity = time.monotonic()
            await hub.send("transcription_received", {"text": text})

            t_resp0 = time.monotonic()
            category = safety.classify_input(text)
            if category is not None:
                response = persona.safety_fallbacks.get(category) or persona.safety_fallbacks.get(
                    "inappropriate", ""
                )
                source = "fallback"
            else:
                await set_state("thinking")
                # RAG'i GROUNDING + offline-yedek için çalıştır (cevabı VERBATIM
                # döndürmek için değil).
                try:
                    results = await asyncio.wait_for(
                        rag.query(text, persona.id, top_k=3), RAG_TIMEOUT
                    )
                except TimeoutError:
                    print("[rag timeout] arama asıldı")
                    results = []

                # LLM-ÖNCELİKLİ: her zaman Sonnet'e sor — sohbet bağlamı (son 6 tur)
                # + RAG adaylarını grounding ver. Sonnet kurate bilgiyi BAĞLAMA göre
                # harmanlar (kör kelime-eşleşmesi değil; "context'i anlayan" davranış).
                ctx = ConversationContext(
                    session_id=kiosk.session_id or "kiosk", persona_id=persona.id
                )
                ctx.turns = list(kiosk.history[-6:])
                # Grounding: yalnız ALAKA eşiğini geçen adayları ver (çok düşük
                # skorlu/alakasız aday Sonnet'i yanıltmasın). Alakalı varsa Sonnet
                # kurate bilgiyi bağlama göre harmanlar; yoksa persona bilgisiyle cevaplar.
                ctx.retrieved = [
                    r.response_text for r in results[:3]
                    if r.similarity >= GROUNDING_MIN
                ] if results else []

                async def _collect_llm(_text: str, _ctx: ConversationContext) -> str:
                    parts: list[str] = []
                    async for tok in llm.generate_response(_text, persona, _ctx):
                        parts.append(tok)
                    return "".join(parts).strip()

                try:
                    raw = await asyncio.wait_for(_collect_llm(text, ctx), LLM_TIMEOUT)
                    # sahne yönergesi/jest temizle (TTS sadece SÖZÜ okusun)
                    clean = _strip_stage_directions(raw)
                    response = safety.check_output(trim_to_last_sentence(clean), persona).text
                    source = "generated"
                    if not kiosk.llm_ok:  # internet/LLM geri geldi
                        kiosk.llm_ok = True
                        await hub.send("llm_ok", {})
                        print("[ÇEVRİMİÇİ] LLM erişimi geri geldi")
                except Exception as exc:  # noqa: BLE001 — TimeoutError dahil
                    # OFFLINE/HATA YEDEĞİ (internet yok): lokal RAG + statik ile İDARE ET.
                    if kiosk.llm_ok:
                        kiosk.llm_ok = False
                        await hub.send("llm_offline", {})
                        print("[ÇEVRİMDIŞI] LLM erişilemiyor — lokal RAG+statik yedeğe geçildi")
                    # Offline'da DAHA CÖMERT eşik: ilgili kurate cevabı yakala (genel
                    # fallback yerine gerçek bilgi). Online grounding bundan etkilenmez.
                    hit = (
                        results
                        and results[0].similarity >= OFFLINE_FALLBACK_THRESHOLD
                        and (len(results) < 2
                             or (results[0].similarity - results[1].similarity) >= margin)
                    )
                    if hit:
                        response = safety.check_output(results[0].response_text, persona).text
                        source = "rag:offline"
                    else:
                        # konuyu kapsanan alanlara çeken DEĞİŞKEN nazik yönlendirme
                        idx = (len(kiosk.history) // 2) % len(OFFLINE_DEFLECTIONS)
                        response = OFFLINE_DEFLECTIONS[idx]
                        source = "fallback:offline"
                    print(f"[llm yedek] {type(exc).__name__} -> {source}")

            print(f"[zaman] yanıt({source})={time.monotonic() - t_resp0:.2f}s")
            print(f"[El-Cezerî/{source}] {response}")
            await hub.send("llm_response_completed", {"source": source, "text": response})
            kiosk.history.append(DialogueTurn(role="visitor", text=text))
            kiosk.history.append(DialogueTurn(role="persona", text=response))
            del kiosk.history[:-20]
            kiosk.last_activity = time.monotonic()

            await set_state("speaking")
            if active():
                barged = await synth_and_play(response)
                if barged:
                    print("[barge-in] ziyaretçi araya girdi — dinlemeye dönülüyor")
            await asyncio.sleep(cooldown)
            mic.drain()
            kiosk.last_activity = time.monotonic()
            await set_state("listening")
        except Exception as exc:  # noqa: BLE001
            if _is_oom(exc):
                print(f"[OOM] CUDA bellek doldu — empty_cache + toparlanma: {exc}")
                _empty_cuda_cache()
                await asyncio.sleep(1.5)
            else:
                print(f"[döngü hata] {type(exc).__name__}: {exc}")
                await asyncio.sleep(0.5)


async def _boot(cfg) -> tuple[Any, ...]:
    persona = get_persona("cezeri")
    print(f"[1/4] TTS ({cfg.tts.provider}) yükleniyor...")
    tts = ProviderFactory.create_tts(cfg.tts)
    async for _ in tts.synthesize_stream("Bir, iki, üç.", "cezeri"):
        pass
    print("[2/4] Whisper STT yükleniyor...")
    stt_kwargs = {**cfg.stt.config}
    stt_kwargs.setdefault("flush_on_stream_end", True)
    stt = WhisperLocalSTT(WhisperConfig(**stt_kwargs))
    await stt._ensure_model()
    print("[3/4] RAG (e5 + reranker) yükleniyor...")
    rag = ChromaRAGStore({
        "store_path": cfg.llm.rag.store_path,
        "candidate_pool": cfg.llm.rag.candidate_pool,
        "embedding_device": cfg.llm.rag.embedding_device,
        "use_reranker": cfg.llm.rag.use_reranker,
        "reranker_model": cfg.llm.rag.reranker_model,
    })
    await rag._ensure_ready()
    await rag.query("merhaba", "cezeri", top_k=1)
    print(f"[4/4] LLM ({cfg.llm.provider}) hazırlanıyor...")
    llm = ProviderFactory.create_llm(cfg.llm)
    llm_ok = True
    if hasattr(llm, "warmup"):
        try:
            await llm.warmup(persona)
        except Exception as exc:  # noqa: BLE001
            llm_ok = False
            print("\n" + "!" * 64)
            print("[LLM UYARI] Claude AUTH/bağlantı doğrulanamadı — UZUN-KUYRUK")
            print("            sorular GENERIC fallback alacak. RAG-hit cevaplar çalışır.")
            print(f"            sebep: {str(exc)[:140]}")
            print("!" * 64 + "\n")
    return stt, tts, rag, llm, SafetyFilter(), persona, llm_ok


async def _calibrate(mic: E.MicStream, opts: Opts) -> E.Calibration:
    """Açılış kalibrasyonu — yalnız ORTAM ölçümü (hoparlör açılmaz → boot ASLA
    donmaz). Eko/reverb ölçümü teşhis aracına (audio_check) bırakıldı; barge
    referans-kapısı güvenli ref_quiet/echo_guard varsayılanlarıyla yine sağlam
    çalışır (echo_active TTS çıkış zarfını kullanır, mik eko'sunu değil)."""
    if not opts.do_calibrate:
        print("[kalibrasyon] atlandı (--no-calibrate) — statik/override eşikler.", flush=True)
        return E.default_calibration(mic.native_sr, opts.overrides)
    print(
        f"\n[kalibrasyon] Ortam gürültüsü ölçülüyor ({opts.ambient_seconds:.0f} sn) — "
        "LÜTFEN SESSİZ OLUN...", flush=True,
    )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                E.calibrate, mic, ambient_seconds=opts.ambient_seconds,
                speaker=None, test_pcm=None, overrides=opts.overrides,
            ),
            timeout=opts.ambient_seconds + 8.0,
        )
    except Exception as exc:  # noqa: BLE001 — boot ASLA donmamalı; her hâlde devam
        print(f"[kalibrasyon] ölçüm başarısız ({exc}); statik varsayılanlara düşülüyor.")
        return E.default_calibration(mic.native_sr, opts.overrides)


async def _shutdown(providers: dict[str, Any], mic: E.MicStream) -> None:
    for name in ("tts", "stt", "llm", "rag"):
        prov = providers.get(name)
        if prov is not None and hasattr(prov, "close"):
            with contextlib.suppress(Exception):
                await prov.close()
    _empty_cuda_cache()
    with contextlib.suppress(Exception):
        mic.stop()


async def main(opts: Opts) -> int:
    configure_logging(level="WARNING")
    _ensure_api_key()
    cfg = get_config()

    problems = validate_startup(cfg)
    if problems:
        print("\n" + "=" * 64)
        print("[AÇILIŞ DOĞRULAMA] eksikler bulundu:")
        for p in problems:
            print(f"  - {p}")
        print("=" * 64)
        if cfg.app.environment == "production":
            print("[HATA] production profilinde eksiklerle açılmıyor. Düzelt ve tekrar başlat.")
            return 1
        print("[uyarı] development profili — yine de devam ediliyor.\n")

    print("\n=== BilimFest Festival Kiosk ===")
    stt, tts, rag, llm, safety, persona, llm_ok = await _boot(cfg)

    mic = E.MicStream(opts.device)
    mic.start()

    cal = await _calibrate(mic, opts)
    print("\n" + "-" * 60)
    print("[kalibrasyon] " + cal.summary())
    for note in cal.notes:
        print(f"   - {note}")
    print("-" * 60, flush=True)
    print()
    endpointer = E.Endpointer(cal, opts.endpoint)
    barge = E.BargeDetector(cal, opts.barge_cfg)

    hub = Hub()
    kiosk = Kiosk(persona_id="cezeri", session_id=uuid.uuid4().hex, llm_ok=llm_ok)
    op_token = os.environ.get("BFEST_OP_TOKEN", "1206")
    app = build_app(hub, kiosk, op_token)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=opts.port, log_level="warning")  # noqa: S104
    )
    providers = {"stt": stt, "tts": tts, "rag": rag, "llm": llm}

    tasks = [
        asyncio.create_task(server.serve(), name="server"),
        asyncio.create_task(
            audio_loop(kiosk, hub, stt=stt, tts=tts, rag=rag, llm=llm, safety=safety,
                       persona=persona, cfg=cfg, mic=mic, endpointer=endpointer,
                       barge=barge, opts=opts),
            name="audio_loop",
        ),
        asyncio.create_task(vram_monitor(), name="vram_monitor"),
        asyncio.create_task(mic_watchdog(mic, hub, kiosk), name="mic_watchdog"),
    ]
    exit_code = 0
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            name = t.get_name()
            with contextlib.suppress(asyncio.CancelledError):
                exc = t.exception()
                if exc is not None:
                    print(f"[supervisor] '{name}' hatayla bitti: {exc}")
                    exit_code = 1
                else:
                    print(f"[supervisor] '{name}' beklenmedik şekilde bitti — kapatılıyor")
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await _shutdown(providers, mic)
    return exit_code


def _build_opts(args: argparse.Namespace) -> Opts:
    silence_ms = int(args.silence * 1000)
    endpoint = E.EndpointConfig(
        onset_ms=args.onset_ms,
        silence_ms=silence_ms,
        silence_ms_short=max(silence_ms + 400, E.EndpointConfig().silence_ms_short),
        max_listen_ms=int(args.max_listen * 1000),
    )
    barge_cfg = E.BargeConfig(barge_ms=args.barge_ms)
    # Sadece AÇIKÇA verilen eşikler kalibrasyonu ezer (default None -> ölçülen kalır).
    overrides: dict[str, float] = {}
    for flag, key in (
        (args.threshold, "listen_threshold"),
        (args.min_voice, "min_voice_rms"),
        (args.barge_threshold, "barge_threshold"),
        (args.barge_ref_quiet, "barge_ref_quiet"),
        (args.barge_echo_guard, "echo_guard_ms"),
    ):
        if flag is not None:
            overrides[key] = flag
    dev: Any = args.device
    if dev is not None and str(dev).isdigit():
        dev = int(dev)
    return Opts(
        device=dev, port=args.port, barge=args.barge,
        do_calibrate=not args.no_calibrate, ambient_seconds=args.ambient,
        min_confidence=args.min_confidence, endpoint=endpoint, barge_cfg=barge_cfg,
        overrides=overrides,
    )


if __name__ == "__main__":
    _safe_console()
    ap = argparse.ArgumentParser(description="BilimFest festival kiosk (mic + ses + web)")
    ap.add_argument("--device", default=None, help="mikrofon cihaz index'i (audio_check ile bul)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--barge", action=argparse.BooleanOptionalAction, default=False,
                    help="barge-in (araya girince kesme). ŞİMDİLİK VARSAYILAN KAPALI; "
                         "açmak için --barge, kapatmak için --no-barge")
    ap.add_argument("--no-calibrate", action="store_true",
                    help="açılış kalibrasyonunu atla (statik/override eşik kullan)")
    ap.add_argument("--ambient", type=float, default=3.0, help="ortam ölçüm süresi (sn)")
    # endpointing
    ap.add_argument("--silence", type=float, default=1.2, help="duraklama payı (sn) — bitti sayma")
    ap.add_argument("--max-listen", type=float, default=15.0, help="tek söyleyiş üst sınırı (sn)")
    ap.add_argument("--onset-ms", type=int, default=120, help="konuşma başlangıcı min süre (ms)")
    ap.add_argument("--barge-ms", type=int, default=450, help="gereken sürekli ziyaretçi konuşması (ms)")
    ap.add_argument("--min-confidence", type=float, default=0.0, help="STT güven eşiği (0=kapalı)")
    # kalibrasyonu ezen eşikler (verilmezse ÖLÇÜLEN kullanılır)
    ap.add_argument("--threshold", type=float, default=None, help="[override] dinleme VAD eşiği (RMS)")
    ap.add_argument("--min-voice", type=float, default=None, help="[override] gürültü-kapısı RMS")
    ap.add_argument("--barge-threshold", type=float, default=None,
                    help="[override] ziyaretçi barge eşiği")
    ap.add_argument("--barge-ref-quiet", type=float, default=None,
                    help="[override] TTS çıkış RMS < bu => barge armed")
    ap.add_argument("--barge-echo-guard", type=int, default=None,
                    help="[override] El-Cezerî sustuktan sonra reverb beklemesi (ms)")
    args = ap.parse_args()
    try:
        raise SystemExit(asyncio.run(main(_build_opts(args))))
    except KeyboardInterrupt:
        print("\n[festival] kapatıldı.")
