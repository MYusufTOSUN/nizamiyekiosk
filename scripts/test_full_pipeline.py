"""Uçtan uca canlı pipeline REPL'i.

Tek bir process'te modeller bir kere yüklenir, sonsuza kadar **Enter → konuş →
cevap → tekrar** döngüsü. Cold-start cezası ilk turda; sonrasında her tur
yalnızca pure inference latency'sini gösterir.

Kullanım:
    python scripts/test_full_pipeline.py [--seconds 8] [--device 1]

Çıkış: Ctrl+C ya da boş Enter.
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

from src.core.config import get_config
from src.core.interfaces import SessionState
from src.core.logger import configure_logging
from src.intent.detector import KeywordIntentDetector
from src.llm.llama_local import LlamaConfig, LlamaLocalLLM
from src.llm.persona import get_persona
from src.llm.rag_store import ChromaRAGStore
from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT
from src.tts.xtts_local import XTTS_NATIVE_SR, XTTSConfig, XTTSLocalTTS

PCM_CHUNK_SAMPLES = 320


class PipelineState:
    """Yüklü modeller — tüm turlar boyunca paylaşılır."""

    def __init__(self) -> None:
        self.tts: XTTSLocalTTS | None = None
        self.stt: WhisperLocalSTT | None = None
        self.rag: ChromaRAGStore | None = None
        self.detector: KeywordIntentDetector = KeywordIntentDetector()
        self.llm: LlamaLocalLLM | None = None  # ilk RAG miss'te lazy yükle


async def warmup_tts(tts: XTTSLocalTTS, voice_id: str) -> None:
    """TTS'in JIT compile + ilk inference cezasını yutmak için kısa dummy."""
    dummy = "Bir, iki, üç."
    async for _ in tts.synthesize_stream(dummy, voice_id):
        pass


async def boot(cfg: Any) -> PipelineState:
    """Modelleri **doğru sırada** yükle: XTTS önce (torch cuDNN sahnede), Whisper sonra."""
    state = PipelineState()

    # 1) XTTS önce — torch cuDNN context'i kurulur, sonra gelen CTranslate2/torch
    # bu kurulu cuDNN'i kullanır, sürüm konflikti çıkmaz.
    print("[1/3] TTS yukleniyor (XTTS-onceden, cuDNN guvende)...")
    t0 = time.perf_counter()
    state.tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    await state.tts._ensure_model()
    print(f"  TTS yuklendi ({int(time.perf_counter()-t0)}s)")

    print("[1.5/3] TTS warmup (ilk inference cezasini at)...")
    t0 = time.perf_counter()
    await warmup_tts(state.tts, "cezeri")
    print(f"  Warmup tamam ({int((time.perf_counter()-t0)*1000)}ms)")

    # 2) Whisper — CTranslate2 backend, cuDNN zaten torch'tan yüklü
    print("[2/3] Whisper STT yukleniyor...")
    t0 = time.perf_counter()
    stt_kwargs = {**cfg.stt.config}
    stt_kwargs.setdefault("flush_on_stream_end", True)
    state.stt = WhisperLocalSTT(WhisperConfig(**stt_kwargs))
    await state.stt._ensure_model()
    print(f"  STT yuklendi ({int(time.perf_counter()-t0)}s)")

    # 3) RAG (e5 embedding)
    print("[3/3] RAG embedder + Chroma yukleniyor...")
    t0 = time.perf_counter()
    state.rag = ChromaRAGStore({"store_path": cfg.llm.rag.store_path})
    await state.rag._ensure_ready()
    print(f"  RAG hazir ({int(time.perf_counter()-t0)}s)")

    return state


async def run_turn(
    state: PipelineState,
    cfg: Any,
    seconds: float,
    device: str | int | None,
) -> None:
    import numpy as np
    import sounddevice as sd  # type: ignore[import-not-found]

    assert state.tts and state.stt and state.rag
    persona = get_persona("cezeri")
    assert persona

    audio_q: queue.Queue[bytes | None] = queue.Queue()

    def callback(indata: Any, _frames: int, _ti: Any, status: Any) -> None:
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        mono = indata[:, 0] if indata.ndim > 1 else indata
        audio_q.put(numpy_to_pcm_bytes(np.asarray(mono, dtype=np.float32)))

    async def audio_iter() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, audio_q.get)
            if chunk is None:
                return
            yield chunk

    print(f"\n>>> KONUS ({seconds:.0f}s)...")
    turn_start = time.perf_counter()
    text = ""
    with sd.InputStream(
        samplerate=DEFAULT_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=PCM_CHUNK_SAMPLES,
        device=device,
        callback=callback,
    ):
        stop_t = asyncio.create_task(_stop_after(seconds, audio_q))
        stt_start = time.perf_counter()
        async for r in state.stt.transcribe_stream(audio_iter()):
            text = r.text
            print(f"  [STT {int((time.perf_counter()-stt_start)*1000)}ms] '{text}'")
            break
        stop_t.cancel()
        try:
            await stop_t
        except asyncio.CancelledError:
            pass

    if not text:
        print("  (Bos transcript)")
        return

    intent = await state.detector.detect(text, current_state=SessionState.LISTENING)
    print(f"  [INTENT] {intent.type}")

    rag_start = time.perf_counter()
    results = await state.rag.query(text, persona.id, top_k=3)
    rag_ms = int((time.perf_counter() - rag_start) * 1000)
    response = ""
    if results and results[0].similarity >= cfg.llm.rag.similarity_threshold:
        response = results[0].response_text
        print(f"  [RAG hit sim={results[0].similarity:.2f} {rag_ms}ms]")
    else:
        sim = results[0].similarity if results else 0.0
        print(f"  [RAG miss sim={sim:.2f} < {cfg.llm.rag.similarity_threshold}]")
        if state.llm is None:
            print("  [LLM ilk kez yukleniyor...]")
            state.llm = LlamaLocalLLM(LlamaConfig(**cfg.llm.config))
            await state.llm._ensure_model()
        llm_start = time.perf_counter()
        chunks = []
        first_ms = None
        async for token in state.llm.generate_response(text, persona):
            if first_ms is None:
                first_ms = int((time.perf_counter() - llm_start) * 1000)
            chunks.append(token)
        response = "".join(chunks).strip()
        llm_ms = int((time.perf_counter() - llm_start) * 1000)
        print(f"  [LLM ttfb={first_ms}ms total={llm_ms}ms]")

    print(f"\n  [Cezeri]: {response}")

    tts_start = time.perf_counter()
    pcm_buffer = bytearray()
    first_chunk_ms = None
    async for chunk in state.tts.synthesize_stream(response, persona.voice_id):
        if first_chunk_ms is None:
            first_chunk_ms = int((time.perf_counter() - tts_start) * 1000)
        pcm_buffer.extend(chunk)
    tts_total = int((time.perf_counter() - tts_start) * 1000)

    audio = np.frombuffer(bytes(pcm_buffer), dtype=np.int16).astype(np.float32) / 32768.0
    dur_s = audio.size / XTTS_NATIVE_SR
    print(
        f"  [TTS first={first_chunk_ms}ms total={tts_total}ms audio={dur_s:.2f}s]"
    )
    print("  [Hoparlordan oynatiliyor...]")
    sd.play(audio, samplerate=XTTS_NATIVE_SR, blocking=True)

    total_ms = int((time.perf_counter() - turn_start) * 1000)
    print(f"\n  >>> TUR TOPLAM: {total_ms}ms (konusmadan sese)")


async def _stop_after(seconds: float, q: queue.Queue[bytes | None]) -> None:
    await asyncio.sleep(seconds)
    q.put(None)


async def main(seconds: float, device: str | int | None) -> int:
    configure_logging(level="WARNING")
    cfg = get_config()

    print("\n=== BilimFest tum pipeline REPL ===")
    print("Modeller yukleniyor (ilk acilis ~30-40s)...\n")
    boot_start = time.perf_counter()
    state = await boot(cfg)
    print(f"\nHazir ({int(time.perf_counter()-boot_start)}s). Enter ile yeni tur, Ctrl+C cikis.\n")

    while True:
        try:
            inp = input("Enter > ").strip()
            if inp.lower() in ("q", "quit", "exit"):
                break
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            await run_turn(state, cfg, seconds, device)
        except Exception as exc:  # noqa: BLE001
            print(f"  [HATA] {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BilimFest tum pipeline REPL")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()
    dev = args.device
    if dev is not None:
        try:
            dev = int(dev)
        except ValueError:
            pass
    sys.exit(asyncio.run(main(args.seconds, dev)))
