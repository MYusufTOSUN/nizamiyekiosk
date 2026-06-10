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
import os
import re
import sys
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

# XTTS v2 dil bazlı maksimum karakter sınırı (TTS paketi içinden):
# Türkçe için 226, İngilizce 250. Bunu aşan metin truncate olur veya patlar.
MAX_CHARS_PER_LANGUAGE: dict[str, int] = {
    "tr": 226, "en": 250, "es": 239, "fr": 273, "de": 253,
    "it": 213, "pt": 203, "pl": 224, "ru": 182, "nl": 251,
    "cs": 186, "ar": 166, "zh-cn": 82, "ja": 71, "hu": 224,
    "ko": 95, "hi": 250,
}


def split_text_for_xtts(text: str, language: str = "tr", max_chars: int | None = None) -> list[str]:
    """Uzun metni XTTS limitine sığacak parçalara böl.

    Önce nokta/soru/ünlem'de, sonra virgülde böler. Limit altında kalan
    parçaları birleştirir. Algoritma deterministik.
    """
    limit = max_chars or MAX_CHARS_PER_LANGUAGE.get(language, 200)
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    # Cümle sınırlarında böl
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(sent) > limit:
            # Cümle bile limitten uzunsa virgülde böl
            sub = [p.strip() for p in re.split(r"(?<=[,;:])\s+", sent) if p.strip()]
            for piece in sub:
                if len(buf) + len(piece) + 1 <= limit:
                    buf = (buf + " " + piece).strip()
                else:
                    if buf:
                        chunks.append(buf)
                    if len(piece) > limit:
                        # Hala uzunsa karakter düzeyinde kes (son çare)
                        chunks.extend(_hard_split(piece, limit))
                        buf = ""
                    else:
                        buf = piece
        elif len(buf) + len(sent) + 1 <= limit:
            buf = (buf + " " + sent).strip()
        else:
            if buf:
                chunks.append(buf)
            buf = sent
    if buf:
        chunks.append(buf)
    return chunks


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class XTTSConfig(BaseModel):
    """XTTS v2 ayarları."""

    # Yerel snapshot klasörü (config.json + model.pth + vocab.json bekler).
    # Boş bırakırsan TTS paketi Coqui gateway'inden indirir (yavaş/güvenilmez).
    model_path: str = "data/models/xtts_v2"
    # TTS paketi auto-download için kullanılan ad (fallback).
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
        # Windows: torch CUDA wheel'deki cuDNN/cuBLAS DLL'lerini DLL search'e ekle
        # (XTTS sistem CUDA toolkit yoksa cudnnGetLibConfig'i bulamaz)
        self._inject_torch_cuda_dll_dir()
        device = self._effective_device()

        # Yerel klasör varsa Coqui auto-download'a hiç başvurma — daha hızlı,
        # internet kesintisinde çalışır.
        local_dir = Path(self.config.model_path) if self.config.model_path else None
        if local_dir and (local_dir / "config.json").exists() and (
            local_dir / "model.pth"
        ).exists():
            return self._load_from_local(local_dir, device)

        # Fallback: TTS.api auto-download (Coqui gateway)
        try:
            from TTS.api import TTS  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "TTS_001",
                "Coqui TTS kurulu değil. `pip install -e .[tts]`",
                cause=exc,
            ) from exc
        _log.info("loading_xtts_auto", model=self.config.model_name, device=device)
        try:
            tts = TTS(model_name=self.config.model_name, progress_bar=False)
            tts.to(device)
            return tts
        except Exception as exc:  # noqa: BLE001
            raise TTSError("TTS_001", f"XTTS yüklenemedi: {exc}", cause=exc) from exc

    @staticmethod
    def _inject_torch_cuda_dll_dir() -> None:
        """Windows: torch'un bundle ettiği CUDA/cuDNN DLL'lerini DLL search'e ekle."""
        if not sys.platform.startswith("win"):
            return
        if hasattr(os, "add_dll_directory"):
            try:
                import torch

                torch_lib = Path(torch.__file__).resolve().parent / "lib"
                if torch_lib.exists():
                    os.add_dll_directory(str(torch_lib))
            except ImportError:
                pass
            except (OSError, FileNotFoundError):
                pass

    def _effective_device(self) -> str:
        device = self.config.device
        try:
            import torch  # type: ignore[import-not-found]

            if device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
        except ImportError:
            device = "cpu"
        return device

    def _load_from_local(self, model_dir: Path, device: str) -> Any:
        """Local snapshot'tan doğrudan Xtts modelini yükle (auto-download yok)."""
        try:
            from TTS.tts.configs.xtts_config import XttsConfig  # type: ignore[import-not-found]
            from TTS.tts.models.xtts import Xtts  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "TTS_001",
                "Coqui TTS kurulu değil. `pip install -e .[tts]`",
                cause=exc,
            ) from exc

        _log.info("loading_xtts_local", model_dir=str(model_dir), device=device)
        try:
            config = XttsConfig()
            config.load_json(str(model_dir / "config.json"))
            model = Xtts.init_from_config(config)
            model.load_checkpoint(
                config,
                checkpoint_dir=str(model_dir),
                use_deepspeed=False,
            )
            if device == "cuda":
                model.cuda()
            return _LocalXttsWrapper(model, config, device=device)
        except Exception as exc:  # noqa: BLE001
            raise TTSError("TTS_001", f"XTTS local yüklenemedi: {exc}", cause=exc) from exc

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

        # XTTS dil limitini aşan metni cümle sınırlarında parçala
        pieces = split_text_for_xtts(text, language=self.config.language)
        if not pieces:
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
                cs = self.config.output_chunk_samples
                # _LocalXttsWrapper varsa true streaming, yoksa fallback batch
                has_stream = hasattr(self._model, "tts_stream")

                for idx, piece in enumerate(pieces):
                    kwargs: dict[str, Any] = {
                        "text": piece,
                        "language": self.config.language,
                        "speed": speed,
                    }
                    if speaker_wav:
                        kwargs["speaker_wav"] = speaker_wav
                    elif builtin_speaker:
                        kwargs["speaker"] = builtin_speaker

                    if has_stream:
                        # True streaming: Xtts.inference_stream() chunk yield eder
                        kwargs["stream_chunk_size"] = self.config.stream_chunk_size
                        residual = np.zeros(0, dtype=np.float32)
                        for raw_chunk in self._model.tts_stream(**kwargs):
                            # raw_chunk: torch.Tensor 1D fp32 (sample_rate=24000)
                            if hasattr(raw_chunk, "cpu"):
                                raw_chunk = raw_chunk.cpu().numpy()
                            arr = np.asarray(raw_chunk, dtype=np.float32).reshape(-1)
                            if residual.size:
                                arr = np.concatenate([residual, arr])
                            usable = (arr.size // cs) * cs
                            residual = arr[usable:].copy()
                            for i in range(0, usable, cs):
                                pcm = numpy_to_pcm_bytes(arr[i : i + cs])
                                asyncio.run_coroutine_threadsafe(queue.put(pcm), loop)
                        if residual.size:
                            asyncio.run_coroutine_threadsafe(
                                queue.put(numpy_to_pcm_bytes(residual)), loop
                            )
                    else:
                        audio = self._model.tts(**kwargs)
                        arr = np.asarray(audio, dtype=np.float32)
                        if arr.ndim > 1:
                            arr = arr.mean(axis=1)
                        model_sr = getattr(
                            self._model.synthesizer, "output_sample_rate", XTTS_NATIVE_SR
                        )
                        if model_sr != XTTS_NATIVE_SR:
                            arr = resample(arr, model_sr, XTTS_NATIVE_SR)
                        for i in range(0, arr.size, cs):
                            chunk = arr[i : i + cs]
                            asyncio.run_coroutine_threadsafe(
                                queue.put(numpy_to_pcm_bytes(chunk)), loop
                            )
                    _log.debug(
                        "tts_piece_done",
                        idx=idx + 1,
                        of=len(pieces),
                        chars=len(piece),
                        streaming=has_stream,
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
                        pieces=len(pieces),
                    )
                    first_chunk_logged = True
                yield item
        finally:
            total_ms = int((time.perf_counter() - start) * 1000)
            _log.info("tts_complete", total_ms=total_ms, voice_id=voice_id, pieces=len(pieces))
            await producer_task

    async def close(self) -> None:
        self._model = None
        self._voice_refs.clear()


class _LocalXttsWrapper:
    """TTS.api.TTS arayüzünü taklit eden hafif wrapper.

    XTTSLocalTTS._load_from_local() raw Xtts modelini yükler, bunu
    ``model.tts(text, language, speaker, speaker_wav, speed)`` API'sine
    bağlar; ``synthesize_stream`` aynı kodla iki yükleme tarzıyla da çalışır.

    Gerçek streaming için ``tts_stream()`` — Xtts.inference_stream() üzerinden
    chunk-by-chunk audio yield eder, kullanıcı ilk byte gelir gelmez duyar.

    cuDNN konflikti CUDA inference'i patlatırsa otomatik olarak CPU'ya
    düşer; sonraki çağrılar CPU'da çalışır.
    """

    def __init__(self, model: Any, config: Any, device: str = "cuda") -> None:
        self.model = model
        self.config = config
        self.device = device
        self.synthesizer = type(
            "_Syn", (), {"output_sample_rate": int(getattr(config, "output_sample_rate", 24000))}
        )()
        self._fallback_done = False

    def _resolve_speaker(
        self,
        speaker_wav: Any,
        speaker: str | None,
    ) -> tuple[Any, Any, dict | None]:
        """Speaker latent + embedding tuple döndür."""
        if speaker_wav is not None:
            wavs = speaker_wav if isinstance(speaker_wav, list) else [speaker_wav]
            gpt_cond_latent, speaker_embedding = self.model.get_conditioning_latents(
                audio_path=wavs
            )
            return gpt_cond_latent, speaker_embedding, None

        speakers = getattr(self.model, "speaker_manager", None)
        speakers_dict = getattr(speakers, "speakers", {}) if speakers else {}
        if speaker not in speakers_dict and speakers_dict:
            speaker = next(iter(speakers_dict.keys()))
        if not speakers_dict:
            raise TTSError(
                "TTS_002",
                "Yerel XTTS modelinde dahili speaker yok; speaker_wav vermelisin",
            )
        entry = speakers_dict[speaker]
        return entry["gpt_cond_latent"], entry["speaker_embedding"], speakers_dict

    def tts_stream(
        self,
        text: str,
        language: str = "tr",
        speaker_wav: Any = None,
        speaker: str | None = None,
        speed: float = 1.0,
        stream_chunk_size: int = 20,
    ) -> Any:
        """Generator: Xtts.inference_stream() ile chunk-by-chunk audio."""
        gpt_cond_latent, speaker_embedding, _ = self._resolve_speaker(speaker_wav, speaker)
        if self.device == "cpu":
            if hasattr(gpt_cond_latent, "cpu"):
                gpt_cond_latent = gpt_cond_latent.cpu()
            if hasattr(speaker_embedding, "cpu"):
                speaker_embedding = speaker_embedding.cpu()
        return self.model.inference_stream(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            stream_chunk_size=stream_chunk_size,
            speed=speed,
            enable_text_splitting=False,
        )

    def tts(
        self,
        text: str,
        language: str = "tr",
        speaker_wav: Any = None,
        speaker: str | None = None,
        speed: float = 1.0,
    ) -> Any:
        if speaker_wav is not None:
            wavs = speaker_wav if isinstance(speaker_wav, list) else [speaker_wav]
            gpt_cond_latent, speaker_embedding = self.model.get_conditioning_latents(
                audio_path=wavs
            )
        else:
            speakers = getattr(self.model, "speaker_manager", None)
            speakers_dict = getattr(speakers, "speakers", {}) if speakers else {}
            if speaker not in speakers_dict and speakers_dict:
                speaker = next(iter(speakers_dict.keys()))
            if not speakers_dict:
                raise TTSError(
                    "TTS_002",
                    "Yerel XTTS modelinde dahili speaker yok; speaker_wav vermelisin",
                )
            entry = speakers_dict[speaker]
            gpt_cond_latent = entry["gpt_cond_latent"]
            speaker_embedding = entry["speaker_embedding"]

        try:
            out = self.model.inference(
                text=text,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                speed=speed,
            )
        except RuntimeError as exc:
            msg = str(exc).lower()
            if (
                self.device == "cuda"
                and not self._fallback_done
                and (
                    "cudnn" in msg
                    or "cublas" in msg
                    or "cuda" in msg
                    or "out of memory" in msg
                )
            ):
                _log.warning(
                    "xtts_cuda_fallback_cpu",
                    error=str(exc)[:120],
                    hint="cuDNN/cuBLAS konflikti — XTTS CPU'ya düşürülüyor",
                )
                self.model.cpu()
                self.device = "cpu"
                self._fallback_done = True
                # Tek seferlik yeniden dene
                if speaker_wav is not None:
                    # GPU'da hesaplanan latentleri CPU'ya taşı
                    gpt_cond_latent = gpt_cond_latent.cpu()
                    speaker_embedding = speaker_embedding.cpu()
                else:
                    entry = speakers_dict[speaker]
                    gpt_cond_latent = entry["gpt_cond_latent"].cpu()
                    speaker_embedding = entry["speaker_embedding"].cpu()
                out = self.model.inference(
                    text=text,
                    language=language,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                    speed=speed,
                )
            else:
                raise
        return out["wav"]


__all__ = ["XTTSLocalTTS", "XTTSConfig", "XTTS_NATIVE_SR"]
