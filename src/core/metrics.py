"""Prometheus metrikleri — counter, histogram, gauge tanımları.

Tüm metrikler ``bilimfest_`` ön ekiyle başlar.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)


# --- Counters -------------------------------------------------------------

sessions_total = Counter(
    "bilimfest_sessions_total",
    "Toplam ziyaretçi oturum sayısı",
    ["status"],  # completed | abandoned | error
    registry=REGISTRY,
)

turns_total = Counter(
    "bilimfest_turns_total",
    "Toplam konuşma turu",
    ["persona", "source"],  # source: rag | generated | fallback
    registry=REGISTRY,
)

errors_total = Counter(
    "bilimfest_errors_total",
    "Bileşen başına hata sayısı",
    ["component", "error_type"],
    registry=REGISTRY,
)


# --- Histograms (latency) -------------------------------------------------

_LATENCY_BUCKETS = [100, 200, 500, 1000, 2000, 5000]

stt_latency_ms = Histogram(
    "bilimfest_stt_latency_ms",
    "STT işlem gecikmesi (ms)",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

llm_latency_ms = Histogram(
    "bilimfest_llm_latency_ms",
    "LLM cevap üretim gecikmesi (ms)",
    ["source"],
    buckets=[100, 500, 1000, 2000, 5000, 10000],
    registry=REGISTRY,
)

tts_latency_ms = Histogram(
    "bilimfest_tts_latency_ms",
    "TTS ilk byte gecikmesi (ms)",
    buckets=[100, 200, 500, 1000, 2000],
    registry=REGISTRY,
)

end_to_end_latency_ms = Histogram(
    "bilimfest_end_to_end_latency_ms",
    "Uçtan uca toplam gecikme (ms)",
    buckets=[500, 1000, 1500, 2000, 3000, 5000, 10000],
    registry=REGISTRY,
)


# --- Gauges ---------------------------------------------------------------

current_state = Gauge(
    "bilimfest_current_state",
    "Orchestrator anlık state (sayısal kodlama)",
    registry=REGISTRY,
)

active_persona = Gauge(
    "bilimfest_active_persona",
    "Aktif persona göstergesi (1 = aktif)",
    ["persona"],
    registry=REGISTRY,
)

gpu_vram_usage_mb = Gauge(
    "bilimfest_gpu_vram_usage_mb",
    "Bileşen başına GPU VRAM kullanımı (MB)",
    ["component"],
    registry=REGISTRY,
)


def render_latest() -> tuple[bytes, str]:
    """``GET /api/v1/metrics`` için Prometheus payload + content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "REGISTRY",
    "sessions_total",
    "turns_total",
    "errors_total",
    "stt_latency_ms",
    "llm_latency_ms",
    "tts_latency_ms",
    "end_to_end_latency_ms",
    "current_state",
    "active_persona",
    "gpu_vram_usage_mb",
    "render_latest",
]
