"""Coqui XTTS v2 tabanlı lokal TTS provider.

İki mod:
1. **Ses klonlama**: ``data/voices/<persona>/ref_*.wav`` varsa, referans
   audio'dan persona sesi klonlanır. Sergi öncesi ses tasarımı yapıldığında.
2. **Built-in fallback**: WAV yoksa XTTS'in dahili Türkçe konuşmacısı
   kullanılır. Geliştirme/test sırasında bu yeterli.

Stream: 24 kHz mono PCM 16-bit, 20 ms chunk'lar (480 sample) — Phase 5
lipsync ve hoparlör çıkışına aynı formatta gider.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from src.core.errors import TTSError
from src.core.interfaces import TTSProvider
from src.core.logger import get_logger
from src.core.metrics import tts_latency_ms
from src.stt.audio_utils import numpy_to_pcm_bytes, resample

_log = get_logger(component="tts.xtts")

XTTS_NATIVE_SR = 24000  # XTTS v2 24 kHz çıkış üretir
OUTPUT_CHUNK_SAMPLES = 480  # 20 ms @ 24 kHz — pipeline std.
DEFAULT_BUILTIN_SPEAKER = "Damien Black"  # XTTS v2 dahili erkek ses (geliştirme için)


class XTTSConfig(BaseModel):
    """XTTS v2 ayarları."""

    model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    voices_dir: str = "data/voices"
    device: str = "cuda"            # "cuda" varsa, yoksa "cpu"
    language: str = "tr"
    # Klonlama referans dosyası yoksa kullanılacak dahili speaker adı.
    builtin_speaker: str = DEFAULT_BUILTIN_SPEAKER
    stream_chunk_size: int = 20      # XTTS streaming token sayısı (~200 ms chunk)
    # Çıkış audio chunk boyutu (PCM 16-bit byte cinsinden hedef chunk)
    output_chunk_samples: int = OUTPUT_CHUNK_SAMPLES

    model_config = {"extra": "ignore", "protected_namespaces": ()}


class XTTSLocalTTS(TTSProvider):
    """Coqui XTTS v2 lokal TTS provider."""

    def __init__(self, config: dict[str, Any] | XTTSConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self._voice_refs: dict[str, list[str]] = {}

    @staticmethod
    def _normalize(config: dict[str, Any] | XTTSConfig | None) -> XTTSConfig:
        if config is None:
            return XTTSConfig()
        if isinstance(config, XTTSConfig):
            return config
        return XTTSConfig(**config)

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
                self._voice_refs = self._scan_voice_refs()
        return self._model

    def _load_sync(self) -> Any:
        try:
            from TTS.api import TTS  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "TTS_001",
                "Coqui TTS kurulu değil. `pip install -e .[tts]`",
                cause=exc,
            ) from exc

        device = self.config.device
        try:
            import torch  # type: ignore[import-not-found]

            if device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
        except ImportError:
            device = "cpu"

        _log.info("loading_xtts", model=self.config.model_name, device=device)
        try:
            tts = TTS(model_name=self.config.model_name, progress_bar=False)
            tts.to(device)
            return tts
        except Exception as exc:  # noqa: BLE001
            raise TTSError("TTS_001", f"XTTS yüklenemedi: {exc}", cause=exc) from exc

    def _scan_voice_refs(self) -> dict[str, list[str]]:
        refs: dict[str, list[str]] = {}
        base = Path(self.config.voices_dir)
        if not base.exists():
            return refs
        for char_dir in base.iterdir():
            if not char_dir.is_dir():
                continue
            wavs = sorted(
                p for p in char_dir.glob("ref_*.wav") if p.is_file()
            )
            if wavs:
                refs[char_dir.name] = [str(p) for p in wavs]
        if refs:
            _log.info("voice_refs_loaded", count=sum(len(v) for v in refs.values()))
        return refs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        await self._ensure_model()
        if not text.strip():
            return

        start = time.perf_counter()
        first_chunk_logged = False

        speaker_wav = self._voice_refs.get(voice_id)
        builtin_speaker = self.config.builtin_speaker if not speaker_wav else None

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None | Exception] = asyncio.Queue()

        def producer() -> None:
            try:
                assert self._model is not None
                # XTTS streaming API: bazı sürümler tts.tts'i destekler, bazıları
                # synthesizer.tts_model.inference_stream. En sağlam: tts.tts() sync,
                # sonra biz chunk'lara böleriz.
                kwargs: dict[str, Any] = {
                    "text": text,
                    "language": self.config.language,
                    "speed": speed,
                }
                if speaker_wav:
                    kwargs["speaker_wav"] = speaker_wav
                elif builtin_speaker:
                    kwargs["speaker"] = builtin_speaker

                # tts.tts() float32 numpy döner
                audio = self._model.tts(**kwargs)
                # Numpy array'e çevir, normalize et
                arr = np.asarray(audio, dtype=np.float32)
                if arr.ndim > 1:
                    arr = arr.mean(axis=1)
                # XTTS varsayılan 24 kHz'de döner; başka SR ise resample
                model_sr = getattr(self._model.synthesizer, "output_sample_rate", XTTS_NATIVE_SR)
                if model_sr != XTTS_NATIVE_SR:
                    arr = resample(arr, model_sr, XTTS_NATIVE_SR)

                # Chunk'lara böl + PCM 16-bit'e çevir
                cs = self.config.output_chunk_samples
                for i in range(0, arr.size, cs):
                    chunk = arr[i : i + cs]
                    asyncio.run_coroutine_threadsafe(
                        queue.put(numpy_to_pcm_bytes(chunk)), loop
                    )
            except Exception as exc:  # noqa: BLE001
                err = TTSError("TTS_001", f"XTTS sentez hatası: {exc}", cause=exc)
                asyncio.run_coroutine_threadsafe(queue.put(err), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        producer_task = asyncio.create_task(asyncio.to_thread(producer))

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                if not first_chunk_logged:
                    first_chunk_ms = int((time.perf_counter() - start) * 1000)
                    tts_latency_ms.observe(first_chunk_ms)
                    _log.info(
                        "tts_first_chunk",
                        first_chunk_ms=first_chunk_ms,
                        voice_id=voice_id,
                        cloned=bool(speaker_wav),
                    )
                    first_chunk_logged = True
                yield item
        finally:
            total_ms = int((time.perf_counter() - start) * 1000)
            _log.info("tts_complete", total_ms=total_ms, voice_id=voice_id)
            await producer_task

    async def close(self) -> None:
        self._model = None
        self._voice_refs.clear()


__all__ = ["XTTSLocalTTS", "XTTSConfig", "XTTS_NATIVE_SR"]
