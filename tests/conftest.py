"""Pytest setup — proje kökünü sys.path'e ekler ve test ortamı için mock provider'ları zorlar."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Testler config.yaml'ı yok say — provider'lar mock olsun ki ağır model yüklenmesin.
os.environ.setdefault("BFEST__STT__PROVIDER", "mock")
os.environ.setdefault("BFEST__LLM__PROVIDER", "mock")
os.environ.setdefault("BFEST__TTS__PROVIDER", "mock")
os.environ.setdefault("BFEST__LIPSYNC__PROVIDER", "mock")
os.environ.setdefault("BFEST__UNREAL_BRIDGE__PROVIDER", "mock")
# Cached config sürer; testler arası yeniden okunması için cache'i temizle
from src.core import config as _cfg  # noqa: E402

_cfg._cached = None
