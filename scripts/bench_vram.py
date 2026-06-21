"""Festival VRAM + hiz benchmark'i.

Tam yigini (XTTS -> Llama -> Whisper, orchestrator sirasi) yukler, her asamada
gercek VRAM (nvidia-smi) ve LLM warm tok/s olcer. n_gpu_layers tuning icin.

Kullanim: python scripts/bench_vram.py [n_gpu_layers]
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import torch

NGL = int(sys.argv[1]) if len(sys.argv) > 1 else 12
os.environ.setdefault("COQUI_TOS_AGREED", "1")
_lib = Path(torch.__file__).resolve().parent / "lib"
if _lib.exists():
    os.add_dll_directory(str(_lib))

from faster_whisper import WhisperModel  # noqa: E402

from src.core.config import get_config  # noqa: E402
from src.llm.llama_local import LlamaConfig, LlamaLocalLLM  # noqa: E402
from src.llm.persona import get_persona  # noqa: E402
from src.tts.xtts_local import XTTSConfig, XTTSLocalTTS  # noqa: E402


def vram() -> int:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
        ).strip().splitlines()[0]
        return int(out)
    except Exception:
        return -1


async def main() -> int:
    cfg = get_config()
    persona = get_persona("cezeri")
    print(f"[bench] n_gpu_layers={NGL}  | idle VRAM {vram()} MiB / 8151")

    tts = XTTSLocalTTS(XTTSConfig(**cfg.tts.config))
    try:
        await tts._ensure_model()
        async for _ in tts.synthesize_stream("Bir iki uc dort bes.", "cezeri"):
            pass
    except Exception as e:  # noqa: BLE001
        print(f"  XTTS FAIL: {repr(e)[:120]}")
        return 1
    print(f"  after XTTS         : {vram()} MiB")

    lcfg = dict(cfg.llm.config)
    lcfg["n_gpu_layers"] = NGL
    llm = LlamaLocalLLM(LlamaConfig(**lcfg))
    try:
        await llm._ensure_model()
    except Exception as e:  # noqa: BLE001
        print(f"  LLAMA FAIL: {repr(e)[:120]}")
        return 1
    print(f"  after Llama        : {vram()} MiB")

    wc = cfg.stt.config
    try:
        wm = WhisperModel(
            wc["model"], device="cuda", compute_type=wc.get("compute_type", "int8_float16"),
            download_root=wc.get("model_dir"),
        )
        segs, info = wm.transcribe("data/test_cezeri.wav", language="tr", beam_size=5)
        txt = " ".join(s.text for s in segs).strip()
    except Exception as e:  # noqa: BLE001
        print(f"  WHISPER FAIL (likely OOM): {repr(e)[:120]}")
        print(f"  >>> n_gpu_layers={NGL} TOO HIGH for 8GB with full stack")
        return 2
    print(f"  after Whisper+stt  : {vram()} MiB  | STT='{txt[:45]}'")

    # warm LLM, then measure tok/s on a real fallback generation
    if persona is not None and hasattr(llm, "warmup"):
        await llm.warmup(persona)
    t0 = time.perf_counter()
    n = 0
    out = []
    async for tok in llm.generate_response("Robot nedir, cocuga kisaca anlat.", persona):
        n += 1
        out.append(tok)
    dt = time.perf_counter() - t0
    peak = vram()
    print(f"  LLM warm           : {n} tok in {dt:.1f}s = {n/max(dt,1e-3):.1f} tok/s")
    print(f"  PEAK VRAM          : {peak} MiB / 8151  (headroom {8151-peak} MiB)")
    print(f"  sample             : {''.join(out)[:80]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
