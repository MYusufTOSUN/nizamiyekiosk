"""llama-cpp-python tabanlı lokal LLM provider.

Pipeline: question + persona + context → Llama chat template → streaming token
çıktısı. Model GGUF dosyasından yüklenir, GPU offload destekli.

Hız hedefi: 8B Q4 @ RTX 4060 Laptop → ~30-50 tok/s, ~1-1.5 s TTFB.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.core.errors import ConfigError, LLMError, validate_model_path
from src.core.interfaces import (
    ConversationContext,
    LLMProvider,
    PersonaConfig,
)
from src.core.logger import get_logger
from src.core.metrics import llm_latency_ms

_log = get_logger(component="llm.llama")


class LlamaConfig(BaseModel):
    """llama-cpp-python parametre seti.

    `model_path` GGUF dosyasının disk yolu. `n_gpu_layers` -1 = tüm katmanlar
    GPU'ya offload (8B Q4 ~5 GB VRAM, RTX 4060 8 GB'a sığar).
    """

    model_path: str = "data/models/llama/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    n_gpu_layers: int = -1
    n_ctx: int = 4096
    n_batch: int = 512
    n_threads: int = 4
    use_mmap: bool = True
    use_mlock: bool = False
    verbose: bool = False

    # Decode parametreleri
    max_tokens: int = 220
    temperature: float = 0.6
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.15
    # Whisper transcription dönen kısa kullanıcı cümleleriyle çalıştığımız için
    # uzun yanıtları kısıtla; persona prompt'unda da "max 3 cümle" diyoruz.
    stop_sequences: list[str] = [
        "<|eot_id|>",
        "<|end_of_text|>",
        "\n\nKullanıcı:",
        "\n\nUser:",
    ]

    model_config = {"extra": "forbid", "protected_namespaces": ()}


class LlamaLocalLLM(LLMProvider):
    """Llama 3.x Instruct chat formatına uygun streaming LLM."""

    def __init__(self, config: dict[str, Any] | LlamaConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        # Prompt cache — aynı system_prompt ile başlayan istekler KV cache reuse
        # ile başlar; ilk token latency ~300 ms düşer.
        self._cached_persona_id: str | None = None

    @staticmethod
    def _normalize(config: dict[str, Any] | LlamaConfig | None) -> LlamaConfig:
        if config is None:
            return LlamaConfig()
        if isinstance(config, LlamaConfig):
            return config
        return LlamaConfig(**config)

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
        return self._model

    @staticmethod
    def _inject_torch_cuda_dll_dir() -> None:
        """Windows: torch'un bundle ettiği CUDA DLL'lerini DLL search'e ekle.

        llama-cpp-python CUDA wheel'i sistem CUDA toolkit yoksa cudart64_12.dll
        gibi DLL'leri bulamaz; torch CUDA wheel bunları .venv/Lib/site-packages/
        torch/lib/ altında getiriyor.
        """
        if not sys.platform.startswith("win"):
            return
        if hasattr(os, "add_dll_directory"):
            try:
                import torch  # type: ignore[import-not-found]

                torch_lib = Path(torch.__file__).resolve().parent / "lib"
                if torch_lib.exists():
                    os.add_dll_directory(str(torch_lib))
            except ImportError:
                pass
            except (OSError, FileNotFoundError):
                pass

    def _load_sync(self) -> Any:
        # Windows: llama-cpp-python CUDA wheel'i sistem CUDA toolkit yoksa
        # PyTorch'un bundle ettiği DLL'lere muhtaç. Importtan önce DLL search
        # path'e torch/lib ekle.
        self._inject_torch_cuda_dll_dir()
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
        except (ImportError, RuntimeError) as exc:
            raise LLMError(
                "LLM_001",
                f"llama-cpp-python yüklenemedi: {exc}. README Phase 3 / LLM'e bak.",
                cause=exc,
            ) from exc

        try:
            model_path = validate_model_path(self.config.model_path)
        except ConfigError as exc:
            raise LLMError("LLM_001", str(exc), cause=exc) from exc
        if not model_path.exists():
            raise LLMError(
                "LLM_001",
                "GGUF dosyası bulunamadı. README Phase 3 / LLM 'Llama 8B indir'e bak.",
            )

        _log.info(
            "loading_llama",
            model_path=str(model_path),
            n_gpu_layers=self.config.n_gpu_layers,
            n_ctx=self.config.n_ctx,
        )
        try:
            return Llama(
                model_path=str(model_path),
                n_gpu_layers=self.config.n_gpu_layers,
                n_ctx=self.config.n_ctx,
                n_batch=self.config.n_batch,
                n_threads=self.config.n_threads,
                use_mmap=self.config.use_mmap,
                use_mlock=self.config.use_mlock,
                verbose=self.config.verbose,
                chat_format="llama-3",
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError("LLM_001", f"Llama yüklenemedi: {exc}", cause=exc) from exc

    def _build_messages(
        self,
        question: str,
        persona: PersonaConfig,
        context: ConversationContext | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": persona.system_prompt}
        ]
        if context and context.turns:
            # Son N tur (token sınırını aşmamak için makul kısa tut)
            recent = context.turns[-6:]
            for turn in recent:
                role = "assistant" if turn.role == "persona" else "user"
                messages.append({"role": role, "content": turn.text})
        messages.append({"role": "user", "content": question})
        return messages

    async def generate_response(
        self,
        question: str,
        persona: PersonaConfig,
        context: ConversationContext | None = None,
    ) -> AsyncIterator[str]:
        await self._ensure_model()
        messages = self._build_messages(question, persona, context)

        start = time.perf_counter()
        first_token_logged = False
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def _safe_put(item: Any) -> bool:
            """Loop kapanmışsa sessizce çık (None döndür); aksi halde put başarılı."""
            try:
                asyncio.run_coroutine_threadsafe(queue.put(item), loop)
                return True
            except RuntimeError as exc:
                if "loop is closed" in str(exc).lower() or "Loop is closed" in str(exc):
                    return False
                raise

        def producer() -> None:
            try:
                assert self._model is not None
                stream = self._model.create_chat_completion(
                    messages=messages,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                    top_k=self.config.top_k,
                    repeat_penalty=self.config.repeat_penalty,
                    stop=self.config.stop_sequences,
                    stream=True,
                )
                for event in stream:
                    delta = event["choices"][0].get("delta", {})  # type: ignore[index]
                    chunk = delta.get("content") if isinstance(delta, dict) else None
                    if chunk:
                        if not _safe_put(chunk):
                            return  # event loop kapandı; thread temiz çıkış
            except Exception as exc:  # noqa: BLE001
                _safe_put(("__ERR__", type(exc).__name__, str(exc)))
            finally:
                _safe_put(None)

        producer_task = asyncio.create_task(asyncio.to_thread(producer))

        try:
            while True:
                token = await queue.get()
                if token is None:
                    break
                if isinstance(token, tuple) and len(token) == 3 and token[0] == "__ERR__":
                    _, err_type, err_msg = token
                    raise LLMError("LLM_003", f"Llama {err_type}: {err_msg}")
                if not isinstance(token, str):
                    continue
                if not first_token_logged:
                    first_token_ms = int((time.perf_counter() - start) * 1000)
                    _log.info("llm_first_token", first_token_ms=first_token_ms)
                    first_token_logged = True
                yield token
        finally:
            total_ms = int((time.perf_counter() - start) * 1000)
            llm_latency_ms.labels(source="generated").observe(total_ms)
            _log.info(
                "llm_complete",
                total_ms=total_ms,
                persona=persona.id,
            )
            await producer_task

    async def warmup(self, persona: PersonaConfig) -> int:
        """Tek bir kısa generate ile JIT cache + tokenizer warm-up.

        Dönüş: ilk inference latency (ms). Persona system_prompt'u KV cache'e
        konuyor; sonraki istek aynı persona ile başlayınca prefix reuse olur.
        """
        await self._ensure_model()
        start = time.perf_counter()
        messages = [
            {"role": "system", "content": persona.system_prompt},
            {"role": "user", "content": "Selam."},
        ]
        try:
            assert self._model is not None
            await asyncio.to_thread(
                self._model.create_chat_completion,
                messages=messages,
                max_tokens=4,
                temperature=0.0,
                stream=False,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("warmup_failed", error=str(exc))
        self._cached_persona_id = persona.id
        return int((time.perf_counter() - start) * 1000)

    async def close(self) -> None:
        # llama-cpp-python __del__ memory'yi temizler — burada referansı bırak.
        # Açıkça GC tetikle: 28-saat sergi için kümülatif cache temizliği.
        self._model = None
        self._cached_persona_id = None
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


__all__ = ["LlamaLocalLLM", "LlamaConfig"]
