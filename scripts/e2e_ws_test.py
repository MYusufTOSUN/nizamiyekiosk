"""Uctan uca pipeline testi: test WAV'i /ws/audio'dan akit, cevabi al.

uvicorn calisirken: STT -> intent -> RAG/LLM -> TTS hepsi TEK surecte calisir
(Whisper CTranslate2 + XTTS torch cuDNN bir-arada-yasama dahil).

Kullanim: python scripts/e2e_ws_test.py [wav_path]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import numpy as np
import soundfile as sf
import websockets
from scipy.signal import resample_poly

WAV = sys.argv[1] if len(sys.argv) > 1 else "data/test_cezeri.wav"
BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws/audio"
TARGET_SR = 16000


async def wait_health(timeout: float = 200.0) -> bool:
    start = time.time()
    async with httpx.AsyncClient() as c:
        while time.time() - start < timeout:
            try:
                r = await c.get(f"{BASE}/health", timeout=5)
                if r.status_code == 200:
                    print(f"[health] {r.json()} (after {time.time()-start:.0f}s)")
                    return True
            except Exception:
                pass
            await asyncio.sleep(3)
    return False


def load_pcm16(path: str) -> bytes:
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != TARGET_SR:
        g = np.gcd(int(sr), TARGET_SR)
        mono = resample_poly(mono, TARGET_SR // g, sr // g)
    pcm = np.clip(mono, -1.0, 1.0)
    return (pcm * 32767.0).astype("<i2").tobytes()


async def main() -> int:
    print(f"[e2e] waiting for orchestrator warmup at {BASE}/health ...")
    if not await wait_health():
        print("[e2e] FAIL: /health never ready")
        return 1
    pcm = load_pcm16(WAV)
    print(f"[e2e] streaming {len(pcm)} bytes ({len(pcm)/2/TARGET_SR:.2f}s @ {TARGET_SR}Hz) over WS")
    chunk = 640  # 20 ms @ 16k mono int16
    t0 = time.time()
    async with websockets.connect(WS, max_size=None, open_timeout=30) as ws:
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i : i + chunk])
            await asyncio.sleep(0.005)
        await ws.send("__end__")
        print("[e2e] audio sent, awaiting turn_completed ...")
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=180)
            msg = json.loads(raw)
            if msg.get("type") == "turn_completed":
                dt = time.time() - t0
                print("=" * 60)
                print(f"  transcription : {msg.get('transcription')!r}")
                print(f"  intent        : {(msg.get('intent') or {}).get('type')}")
                print(f"  source        : {msg.get('source')}")
                print(f"  response      : {msg.get('response')!r}")
                print(f"  audio_bytes   : {msg.get('audio_bytes')}")
                print(f"  latency_ms    : {msg.get('latency_ms')}  (wall {dt*1000:.0f}ms)")
                print("=" * 60)
                return 0
            if msg.get("type") == "error":
                print(f"[e2e] PIPELINE ERROR: {msg.get('message')}")
                return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
