"""Uçtan uca canlı pipeline testi — mikrofon → Whisper → RAG/LLM → XTTS → hoparlör.

PC versiyonu için en yakın gerçek senaryo. Pepper's Ghost yok, MetaHuman yok,
sadece sesli sohbet. Cezerî persona ile çalışır.

Kullanım:
    python scripts/test_full_pipeline.py [--seconds 8] [--device 1]
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
from src.core.logger import configure_logging, get_logger
from src.intent.detector import KeywordIntentDetector
from src.llm.llama_local import LlamaConfig, LlamaLocalLLM
from src.llm.persona import get_persona
from src.llm.rag_store import ChromaRAGStore
from src.stt.audio_utils import DEFAULT_SAMPLE_RATE, numpy_to_pcm_bytes
from src.stt.whisper_local import WhisperConfig, WhisperLocalSTT
from src.tts.xtts_local import XTTS_NATIVE_SR, XTTSConfig, XTTSLocalTTS

PCM_CHUNK_SAMPLES = 320


async def main(seconds: float, device: str | int | None) -> int:
    try:
        import numpy as np
        import sounddevice as sd  # type: ignore[import-not-found]
    except ImportError:
        print("HATA: numpy/sounddevice yok. pip install -e .[stt]")
        return 1

    cfg = get_config()
    configure_logging(level="WARNING")  # az gürültü
    log = get_logger(component="full_pipeline")

    persona = get_persona("cezeri")
    if persona is None:
        print("HATA: cezeri persona yok.")
        return 1

    # 8 GB VRAM'a Whisper + Llama + e5 + XTTS aynı anda sığmıyor.
    # Sıralı yükleme/boşaltma stratejisi:
    #   1. Whisper + e5 yüklü kalır (toplam ~3.5 GB)
    #   2. LLM gerekliyse yükle, üret, boşalt (~5 GB)
    #   3. TTS yükle, sentezle (~2 GB)
    print("Whisper + RAG embedding yukleniyor...")
    stt_kwargs = {**cfg.stt.config}
    stt_kwargs.setdefault("flush_on_stream_end", True)
    stt = WhisperLocalSTT(WhisperConfig(**stt_kwargs))
    rag = ChromaRAGStore({"store_path": cfg.llm.rag.store_path})
    detector = KeywordIntentDetector()

    t0 = time.perf_counter()
    await stt._ensure_model()
    await rag._ensure_ready()
    print(f"Hazir ({int(time.perf_counter()-t0)}s).")

    # ----- 1) Mikrofondan kaydet -----
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

    with sd.InputStream(
        samplerate=DEFAULT_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=PCM_CHUNK_SAMPLES,
        device=device,
        callback=callback,
    ):
        print(f"\nKONUS ({seconds:.0f}s)...")
        stop_t = asyncio.get_running_loop().create_task(_stop_after(seconds, audio_q))
        stt_start = time.perf_counter()
        text = ""
        async for r in stt.transcribe_stream(audio_iter()):
            text = r.text
            print(f"\n[STT {int((time.perf_counter()-stt_start)*1000)}ms] '{text}'")
            break
        stop_t.cancel()
        try:
            await stop_t
        except asyncio.CancelledError:
            pass

    if not text:
        print("Bos transcript. Yarida kestin galiba.")
        return 0

    # ----- 2) Intent -----
    intent = await detector.detect(text, current_state=SessionState.LISTENING)
    print(f"[INTENT] {intent.type} target={intent.target}")

    # ----- 3) RAG -----
    rag_start = time.perf_counter()
    results = await rag.query(text, persona.id, top_k=3)
    rag_ms = int((time.perf_counter() - rag_start) * 1000)
    response = ""
    source = "generated"
    if results and results[0].similarity >= cfg.llm.rag.similarity_threshold:
        response = results[0].response_text
        source = "rag"
        print(f"[RAG hit sim={results[0].similarity:.2f} {rag_ms}ms]")
    else:
        sim = results[0].similarity if results else 0.0
        print(f"[RAG miss sim={sim:.2f} < {cfg.llm.rag.similarity_threshold}]")

    # ----- 4) LLM (RAG miss) -----
    if not response:
        print("[LLM yukleniyor...]")
        llm = LlamaLocalLLM(LlamaConfig(**cfg.llm.config))
        await llm._ensure_model()
        llm_start = time.perf_counter()
        chunks = []
        first_ms = None
        async for token in llm.generate_response(text, persona):
            if first_ms is None:
                first_ms = int((time.perf_counter() - llm_start) * 1000)
            chunks.append(token)
        response = "".join(chunks).strip()
        llm_ms = int((time.perf_counter() - llm_start) * 1000)
        print(f"[LLM ttfb={first_ms}ms total={llm_ms}ms] {len(response)} chars")
        # VRAM bosalt — TTS gelecek
        await llm.close()
        del llm
        _release_gpu()

    print(f"\n[Cezeri]: {response}\n")

    # ----- 5) TTS yukle + sentez + oynat -----
    print("[TTS yukleniyor...]")
    tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    await tts._ensure_model()
    print("[TTS sentez...]")
    tts_start = time.perf_counter()
    pcm_buffer = bytearray()
    first_chunk_ms = None
    async for chunk in tts.synthesize_stream(response, persona.voice_id):
        if first_chunk_ms is None:
            first_chunk_ms = int((time.perf_counter() - tts_start) * 1000)
        pcm_buffer.extend(chunk)
    tts_total = int((time.perf_counter() - tts_start) * 1000)

    audio = np.frombuffer(bytes(pcm_buffer), dtype=np.int16).astype(np.float32) / 32768.0
    dur_s = audio.size / XTTS_NATIVE_SR
    print(f"[TTS first={first_chunk_ms}ms total={tts_total}ms audio={dur_s:.2f}s]")

    print("[Hoparlorden oynatiliyor...]")
    sd.play(audio, samplerate=XTTS_NATIVE_SR, blocking=True)
    print("Bitti.")

    return 0


async def _stop_after(seconds: float, q: queue.Queue[bytes | None]) -> None:
    await asyncio.sleep(seconds)
    q.put(None)


def _release_gpu() -> None:
    """Sıralı yükleme için VRAM serbest bırak."""
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BilimFest tum pipeline canli test")
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
