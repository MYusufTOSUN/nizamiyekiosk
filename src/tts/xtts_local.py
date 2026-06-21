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
import hashlib
import os
import re
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from src.core.errors import ConfigError, TTSError, validate_model_path
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
    # Konuşma hızı çarpanı. 1.0 = referansın doğal hızı. Klon referansı sakin/yavaş
    # okunduysa konuşma yavaş çıkar; 1.1-1.15 hafifçe canlandırır (kalite bozulmaz).
    speed: float = 1.0
    stream_chunk_size: int = 20      # XTTS streaming token sayısı (~200 ms chunk)
    # Çıkış audio chunk boyutu (PCM 16-bit byte cinsinden hedef chunk)
    output_chunk_samples: int = OUTPUT_CHUNK_SAMPLES
    # Disk audio cache — aynı (text, voice_id, speed) sorgusunda XTTS bypass
    cache_enabled: bool = True
    cache_dir: str = "data/tts_cache"
    cache_max_entries: int = 500    # cache büyür → en eski silinir

    model_config = {"extra": "forbid", "protected_namespaces": ()}


def _cache_key(text: str, voice_id: str, speed: float, language: str) -> str:
    """Deterministik hash → cache dosya adı."""
    payload = f"{language}|{voice_id}|{speed:.3f}|{text}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


class XTTSLocalTTS(TTSProvider):
    """Coqui XTTS v2 lokal TTS provider."""

    def __init__(self, config: dict[str, Any] | XTTSConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self._voice_refs: dict[str, list[str]] = {}
        # In-process speaker latent cache: voice_id → (gpt_cond_latent, speaker_embed)
        # B5 optimizasyonu: aynı voice_id için her tts() çağrısında re-compute yok.
        self._speaker_cache: dict[str, tuple[Any, Any]] = {}

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

    async def warmup(self, voice_id: str = "cezeri") -> int:
        """Modeli ÖNCEDEN yükle + ilk inference JIT cezasını yut.

        Startup'ta Whisper'dan ÖNCE çağrılırsa torch cuDNN context'i kurulur,
        sonraki CTranslate2 (Whisper) bunu kullanır → XTTS GPU'da kalır
        (cuDNN konflikti olmaz). Dönüş: warmup süresi (ms).
        """
        import time as _t

        start = _t.perf_counter()
        await self._ensure_model()
        try:
            async for _ in self.synthesize_stream("Bir, iki, üç.", voice_id):
                pass
        except Exception as exc:  # noqa: BLE001
            _log.warning("tts_warmup_failed", error=str(exc))
        return int((_t.perf_counter() - start) * 1000)

    def _load_sync(self) -> Any:
        # Windows: torch CUDA wheel'deki cuDNN/cuBLAS DLL'lerini DLL search'e ekle
        # (XTTS sistem CUDA toolkit yoksa cudnnGetLibConfig'i bulamaz)
        self._inject_torch_cuda_dll_dir()
        # torchaudio 2.9+ (torch 2.11 cu128, Blackwell): load() artik torchcodec
        # ister; ref WAV'lari soundfile ile yukle ki torchcodec/ffmpeg surum
        # bagimliligi olmasin.
        self._patch_torchaudio_soundfile()
        device = self._effective_device()

        # Yerel klasör varsa Coqui auto-download'a hiç başvurma — daha hızlı,
        # internet kesintisinde çalışır.
        local_dir: Path | None = None
        if self.config.model_path:
            try:
                local_dir = validate_model_path(self.config.model_path)
            except ConfigError as exc:
                raise TTSError("TTS_001", str(exc), cause=exc) from exc
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
    def _patch_torchaudio_soundfile() -> None:
        """torchaudio 2.9+ load() torchcodec'e devretti; XTTS ref WAV'larini
        soundfile ile yukle (torchcodec + belirli ffmpeg surumu gerektirmez).

        Idempotent: zaten patch'liyse tekrar sarmaz.
        """
        try:
            import soundfile as sf  # type: ignore[import-not-found]
            import torch as _torch
            import torchaudio  # type: ignore[import-not-found]
        except ImportError:
            return
        if getattr(torchaudio.load, "_bfest_soundfile_patch", False):
            return

        def _sf_load(uri, frame_offset=0, num_frames=-1, normalize=True,  # noqa: ANN001, ANN202
                     channels_first=True, format=None, buffer_size=4096,  # noqa: A002
                     backend=None):
            start = int(frame_offset)
            stop = None if (num_frames is None or num_frames < 0) else start + int(num_frames)
            data, sr = sf.read(str(uri), start=start, stop=stop, dtype="float32", always_2d=True)
            tensor = _torch.from_numpy(data.transpose(1, 0).copy())  # (channels, frames)
            return (tensor if channels_first else tensor.t()), sr

        _sf_load._bfest_soundfile_patch = True  # type: ignore[attr-defined]
        torchaudio.load = _sf_load

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
        speed: float | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        # speed verilmezse config'ten al (klon yavaşsa config.speed=1.1 canlandırır)
        if speed is None:
            speed = self.config.speed

        # Türkçe için sayı/kısaltma normalize: "12. yüzyıl" → "on iki yüzyıl"
        if self.config.language == "tr":
            from src.tts.text_normalize import normalize_for_tts

            text = normalize_for_tts(text)

        # B4: Disk audio cache — RAG hit'ler aynı cevabı üretir, ikinci kullanıcıya
        # XTTS'i hiç çağırma, disk'ten chunk'la stream et.
        cache_path = self._cache_path(text, voice_id, speed)
        if cache_path is not None and cache_path.exists():
            _log.info("tts_cache_hit", key=cache_path.stem, voice_id=voice_id)
            async for chunk in self._stream_cached(cache_path):
                yield chunk
            return

        await self._ensure_model()

        # XTTS dil limitini aşan metni cümle sınırlarında parçala
        pieces = split_text_for_xtts(text, language=self.config.language)
        if not pieces:
            return

        start = time.perf_counter()
        first_chunk_logged = False
        cache_buffer: bytearray | None = bytearray() if cache_path is not None else None

        speaker_wav = self._voice_refs.get(voice_id)
        builtin_speaker = self.config.builtin_speaker if not speaker_wav else None

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None | Exception] = asyncio.Queue()

        def _safe_put(item: Any) -> bool:
            try:
                asyncio.run_coroutine_threadsafe(queue.put(item), loop)
                return True
            except RuntimeError as exc:
                if "loop is closed" in str(exc).lower():
                    return False
                raise

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
                        kwargs["stream_chunk_size"] = self.config.stream_chunk_size
                        residual = np.zeros(0, dtype=np.float32)
                        for raw_chunk in self._model.tts_stream(**kwargs):
                            if hasattr(raw_chunk, "cpu"):
                                raw_chunk = raw_chunk.cpu().numpy()
                            arr = np.asarray(raw_chunk, dtype=np.float32).reshape(-1)
                            if residual.size:
                                arr = np.concatenate([residual, arr])
                            usable = (arr.size // cs) * cs
                            residual = arr[usable:].copy()
                            for i in range(0, usable, cs):
                                if not _safe_put(numpy_to_pcm_bytes(arr[i : i + cs])):
                                    return
                        if residual.size and not _safe_put(numpy_to_pcm_bytes(residual)):
                            return
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
                            if not _safe_put(numpy_to_pcm_bytes(arr[i : i + cs])):
                                return
                    _log.debug(
                        "tts_piece_done",
                        idx=idx + 1,
                        of=len(pieces),
                        chars=len(piece),
                        streaming=has_stream,
                    )
            except Exception as exc:  # noqa: BLE001
                err = TTSError("TTS_001", f"XTTS sentez hatası: {exc}", cause=exc)
                _safe_put(err)
            finally:
                _safe_put(None)

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
                if cache_buffer is not None:
                    cache_buffer.extend(item)
                yield item
        finally:
            total_ms = int((time.perf_counter() - start) * 1000)
            _log.info("tts_complete", total_ms=total_ms, voice_id=voice_id, pieces=len(pieces))
            await producer_task
            if cache_buffer and cache_path is not None:
                try:
                    self._write_cache(cache_path, bytes(cache_buffer))
                except OSError as exc:
                    _log.warning("tts_cache_write_failed", error=str(exc))

    def _cache_path(self, text: str, voice_id: str, speed: float) -> Path | None:
        if not self.config.cache_enabled:
            return None
        key = _cache_key(text, voice_id, speed, self.config.language)
        cache_dir = Path(self.config.cache_dir)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return cache_dir / f"{key}.pcm"

    async def _stream_cached(self, path: Path) -> AsyncIterator[bytes]:
        cs = self.config.output_chunk_samples * 2  # int16 → 2 byte/sample
        with path.open("rb") as f:
            while True:
                chunk = f.read(cs)
                if not chunk:
                    break
                yield chunk
                # event loop'a nefes ver — sd.OutputStream backlog kalmasın
                await asyncio.sleep(0)

    def _write_cache(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)
        self._prune_cache()

    def _prune_cache(self) -> None:
        """En eski cache'leri sil (LRU değil mtime-based, basit)."""
        cache_dir = Path(self.config.cache_dir)
        if not cache_dir.exists():
            return
        files = sorted(cache_dir.glob("*.pcm"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - self.config.cache_max_entries
        for f in files[: max(0, excess)]:
            try:
                f.unlink()
            except OSError:
                pass

    async def close(self) -> None:
        self._model = None
        self._voice_refs.clear()
        self._speaker_cache.clear()
        # GPU memory free (28-saat sergi için kümülatif fragmantasyon korunur).
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


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
        # B5: clone latent cache. Aynı speaker_wav listesi 2. çağrıda recompute yok.
        # Key: tuple(sorted(speaker_wavs)) — list dedup'lu, deterministik.
        self._clone_cache: dict[tuple[str, ...], tuple[Any, Any]] = {}

    def _resolve_speaker(
        self,
        speaker_wav: Any,
        speaker: str | None,
    ) -> tuple[Any, Any, dict[str, Any] | None]:
        """Speaker latent + embedding tuple döndür."""
        if speaker_wav is not None:
            wavs = speaker_wav if isinstance(speaker_wav, list) else [speaker_wav]
            cache_key = tuple(sorted(wavs))
            cached = self._clone_cache.get(cache_key)
            if cached is not None:
                return cached[0], cached[1], None
            gpt_cond_latent, speaker_embedding = self.model.get_conditioning_latents(
                audio_path=wavs
            )
            # B5 cache: aynı ses ref'leri sonraki cevapta recompute edilmez.
            self._clone_cache[cache_key] = (gpt_cond_latent, speaker_embedding)
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
    ):
        """Gercek streaming: ilk audio chunk ~300 ms'de gelir (batch ~1.8 s).

        coqui-tts fork'u (0.25.3) inference_stream'i transformers 4.46 ile uyumlu
        hale getirdi (orijinal TTS 0.22'deki isin_mps_friendly bug'i yok). CUDA
        hatasinda (cuDNN/cuBLAS/OOM) ilk chunk'tan once CPU'ya duser, sonra batch
        uretir; akis ortasi hata nadirdir (GPT forward basinda patlar) -> re-raise.
        """
        gpt_cond_latent, speaker_embedding, _ = self._resolve_speaker(speaker_wav, speaker)
        yielded = False
        try:
            for chunk in self.model.inference_stream(
                text,
                language,
                gpt_cond_latent,
                speaker_embedding,
                stream_chunk_size=stream_chunk_size,
                speed=speed,
            ):
                yielded = True
                yield chunk
            return
        except RuntimeError as exc:
            msg = str(exc).lower()
            recoverable = (
                self.device == "cuda"
                and not self._fallback_done
                and not yielded
                and ("cudnn" in msg or "cublas" in msg or "cuda" in msg or "out of memory" in msg)
            )
            if not recoverable:
                raise
            _log.warning(
                "xtts_stream_cuda_fallback_cpu",
                error=str(exc)[:120],
                hint="cuDNN/cuBLAS/OOM — XTTS CPU'ya düşürülüyor (batch)",
            )
            self.model.cpu()
            self.device = "cpu"
            self._fallback_done = True
            out = self.model.inference(
                text=text,
                language=language,
                gpt_cond_latent=gpt_cond_latent.cpu(),
                speaker_embedding=speaker_embedding.cpu(),
                speed=speed,
            )
            yield out["wav"]

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
