"""Arka plan izleme görevleri — GPU VRAM gauge beslemesi (M3).

`gpu_vram_usage_mb` gauge'i alert kuralının (GPUHighVRAM) dayandığı seri;
hiçbir yer beslemezse alarm asla tetiklenmez. Bu task periyodik olarak
torch.cuda üzerinden VRAM'i okur ve gauge'e yazar.
"""

from __future__ import annotations

import asyncio

from src.core.logger import get_logger
from src.core.metrics import gpu_vram_usage_mb

_log = get_logger(component="orchestrator.monitor")


def _read_vram_mb() -> float | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        # Toplam ayrılmış (allocated + cache) — gerçek baskıyı yansıtır
        reserved = torch.cuda.memory_reserved(0)
        return float(reserved) / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return None


async def gpu_vram_monitor(interval_seconds: float = 10.0) -> None:
    """Periyodik VRAM ölçümü → Prometheus gauge."""
    try:
        while True:
            mb = _read_vram_mb()
            if mb is not None:
                gpu_vram_usage_mb.labels(component="total").set(mb)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        return


__all__ = ["gpu_vram_monitor"]
