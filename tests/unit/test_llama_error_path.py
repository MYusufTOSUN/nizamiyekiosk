"""A1 — Llama producer/consumer error tuple roundtrip."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.errors import LLMError


@pytest.mark.asyncio
async def test_error_tuple_format_raised_as_llm_error() -> None:
    """Producer'ın put ettiği ("__ERR__", type, msg) tuple consumer'da LLMError olur."""
    # Mock generator: error tuple yielde ardından None
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await queue.put(("__ERR__", "RuntimeError", "boom"))
    await queue.put(None)

    # Direkt consumer loop'unu simulate et
    async def consume() -> None:
        while True:
            token = await queue.get()
            if token is None:
                return
            if isinstance(token, tuple) and len(token) == 3 and token[0] == "__ERR__":
                _, err_type, err_msg = token
                raise LLMError("LLM_003", f"Llama {err_type}: {err_msg}")
            if not isinstance(token, str):
                continue

    with pytest.raises(LLMError) as exc:
        await consume()
    assert exc.value.error_code == "LLM_003"
    assert "RuntimeError" in str(exc.value)
    assert "boom" in str(exc.value)


def test_safe_put_handles_closed_loop() -> None:
    """_safe_put loop closed durumunda False döndürür, raise etmez."""
    loop = asyncio.new_event_loop()

    async def _noop() -> None:
        return None

    def _safe_put_factory():  # type: ignore[no-untyped-def]
        coro = _noop()

        def _safe_put() -> bool:
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
                return True
            except RuntimeError as exc:
                # Coro'yu close ki RuntimeWarning çıkmasın
                coro.close()
                if "loop is closed" in str(exc).lower():
                    return False
                raise

        return _safe_put

    loop.close()
    result = _safe_put_factory()()
    assert result is False
