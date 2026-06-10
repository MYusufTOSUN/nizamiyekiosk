"""Festival kurulumdan önce donanım uyumunu doğrula.

Çıktı: ekrana özet + exit code (0 = OK, 1 = uyarı var, 2 = blocker var).

Kullanım:
    python scripts/hardware_check.py
    python scripts/hardware_check.py --profile production  # daha sıkı eşikler
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

Severity = Literal["ok", "warn", "fail"]

# Festival makinesinin sergi-gece eşikleri
PRODUCTION_THRESHOLDS = {
    "min_vram_gb": 16,
    "min_ram_gb": 32,
    "min_disk_gb": 50,
    "required_models": [
        "data/models/llama/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "data/models/whisper/faster-whisper-large-v3",
        "data/models/xtts_v2/model.pth",
        "data/models/embeddings/multilingual-e5-large",
    ],
}

# Geliştirme laptop'u (RTX 4060 8GB)
DEV_THRESHOLDS = {
    "min_vram_gb": 6,
    "min_ram_gb": 12,
    "min_disk_gb": 20,
    "required_models": PRODUCTION_THRESHOLDS["required_models"],
}


def _print(label: str, value: str, sev: Severity) -> None:
    sym = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}[sev]
    print(f"{sym:7} {label:35} {value}")


def check_python() -> Severity:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    _print("Python >=3.11", f"{v.major}.{v.minor}.{v.micro}", "ok" if ok else "fail")
    return "ok" if ok else "fail"


def check_gpu(min_vram_gb: float) -> Severity:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        _print("NVIDIA GPU", "nvidia-smi bulunamadı / GPU yok", "fail")
        return "fail"

    name, mem = out.split(", ", 1)
    mb = int(mem.replace("MiB", "").strip())
    gb = mb / 1024
    if gb < min_vram_gb:
        _print("GPU VRAM", f"{name} {gb:.1f} GB (< {min_vram_gb} GB)", "warn")
        return "warn"
    _print("GPU VRAM", f"{name} {gb:.1f} GB", "ok")
    return "ok"


def check_ram(min_ram_gb: float) -> Severity:
    try:
        import psutil
    except ImportError:
        _print("RAM", "psutil kurulu değil", "warn")
        return "warn"
    gb = psutil.virtual_memory().total / (1024**3)
    if gb < min_ram_gb:
        _print("RAM", f"{gb:.1f} GB (< {min_ram_gb} GB)", "warn")
        return "warn"
    _print("RAM", f"{gb:.1f} GB", "ok")
    return "ok"


def check_disk(min_disk_gb: float) -> Severity:
    free = shutil.disk_usage(".").free / (1024**3)
    if free < min_disk_gb:
        _print("Disk free", f"{free:.1f} GB (< {min_disk_gb} GB)", "warn")
        return "warn"
    _print("Disk free", f"{free:.1f} GB", "ok")
    return "ok"


def check_models(required: list[str]) -> Severity:
    worst: Severity = "ok"
    for rel in required:
        p = Path(rel)
        if not p.exists():
            _print(f"Model {rel}", "EKSİK", "fail")
            worst = "fail"
        else:
            if p.is_dir():
                size_mb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / (
                    1024 * 1024
                )
            else:
                size_mb = p.stat().st_size / (1024 * 1024)
            _print(f"Model {rel}", f"{int(size_mb)} MB", "ok")
    return worst


def check_python_imports() -> Severity:
    needed = [
        ("torch", "torch"),
        ("faster-whisper", "faster_whisper"),
        ("llama-cpp-python", "llama_cpp"),
        ("TTS", "TTS"),
        ("sentence-transformers", "sentence_transformers"),
        ("chromadb", "chromadb"),
        ("fastapi", "fastapi"),
        ("sounddevice", "sounddevice"),
    ]
    worst: Severity = "ok"
    for label, mod in needed:
        try:
            __import__(mod)
            _print(f"Import {label}", "OK", "ok")
        except ImportError as exc:
            _print(f"Import {label}", str(exc)[:50], "fail")
            worst = "fail"
    return worst


def check_cuda_torch() -> Severity:
    try:
        import torch

        if not torch.cuda.is_available():
            _print("torch.cuda.is_available()", "False", "fail")
            return "fail"
        _print("torch.cuda.is_available()", "True", "ok")
        return "ok"
    except ImportError:
        _print("torch", "import failed", "fail")
        return "fail"


def check_cudnn_for_llama() -> Severity:
    """A1/A2 düzeltmelerine rağmen cuDNN sembolü yüklenebiliyor mu."""
    try:
        import os
        import sys
        from pathlib import Path

        import torch  # type: ignore[import-not-found]

        if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
            torch_lib = Path(torch.__file__).resolve().parent / "lib"
            if torch_lib.exists():
                os.add_dll_directory(str(torch_lib))
        from llama_cpp import llama_supports_gpu_offload

        sup = llama_supports_gpu_offload()
        _print("llama-cpp GPU offload", str(sup), "ok" if sup else "warn")
        return "ok" if sup else "warn"
    except Exception as exc:  # noqa: BLE001
        _print("llama-cpp GPU offload", f"ERROR: {exc}"[:50], "warn")
        return "warn"


def check_audio_devices() -> Severity:
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except ImportError:
        _print("Audio devices", "sounddevice yok", "warn")
        return "warn"
    try:
        inp = sd.query_devices(kind="input")
        out = sd.query_devices(kind="output")
        _print("Mic (default)", str(inp.get("name", "?"))[:40], "ok")
        _print("Speaker (default)", str(out.get("name", "?"))[:40], "ok")
        return "ok"
    except Exception as exc:  # noqa: BLE001
        _print("Audio devices", str(exc)[:50], "warn")
        return "warn"


def main() -> int:
    parser = argparse.ArgumentParser(description="BilimFest donanım uyum kontrolü")
    parser.add_argument(
        "--profile",
        choices=["dev", "production"],
        default="dev",
        help="dev = laptop (8GB), production = sergi makinesi (16GB+)",
    )
    args = parser.parse_args()
    thresholds = PRODUCTION_THRESHOLDS if args.profile == "production" else DEV_THRESHOLDS

    print(f"=== BilimFest donanım uyum kontrolü ({args.profile}) ===\n")
    severities: list[Severity] = []
    severities.append(check_python())
    severities.append(check_gpu(thresholds["min_vram_gb"]))
    severities.append(check_ram(thresholds["min_ram_gb"]))
    severities.append(check_disk(thresholds["min_disk_gb"]))
    print()
    severities.append(check_python_imports())
    severities.append(check_cuda_torch())
    severities.append(check_cudnn_for_llama())
    print()
    severities.append(check_audio_devices())
    print()
    severities.append(check_models(thresholds["required_models"]))

    fails = sum(1 for s in severities if s == "fail")
    warns = sum(1 for s in severities if s == "warn")
    print()
    if fails:
        print(f"!! BLOCKER: {fails} kritik problem var, sergi açılamaz.")
        return 2
    if warns:
        print(f"!  {warns} uyarı var, gözden geçir.")
        return 1
    print("OK Donanım uyumlu, sergiye hazır.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
