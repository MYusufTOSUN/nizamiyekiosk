"""structlog tabanlı yapılandırılmış JSON logging.

``session_id``, ``turn_id``, ``component`` gibi context değerleri her loga
otomatik eklenir (``contextvars`` üzerinden).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured: bool = False


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    """Tüm uygulama için structlog ayarlarını uygula. İdempotent."""
    global _configured
    if _configured:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
        structlog.processors.format_exc_info,
    ]
    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(**initial_values: Any) -> structlog.stdlib.BoundLogger:
    """``component=...`` gibi sabit alanlarla logger döndür."""
    if not _configured:
        configure_logging()
    return structlog.get_logger().bind(service="bilimfest", **initial_values)


def bind_context(**values: Any) -> None:
    """Bu task/koroutine için bind edilecek context değerleri."""
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


__all__ = ["configure_logging", "get_logger", "bind_context", "clear_context"]
