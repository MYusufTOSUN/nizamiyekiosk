"""ElevenLabs (cloud) TTS sağlayıcısı — yüksek kaliteli klon/çok-dilli ses.

İshak'ın kendi kaydı ElevenLabs'te klonlanır (Instant/Pro Voice Cloning) ve
``voice_id`` ile burada kullanılır. ``eleven_multilingual_v2`` Türkçe'yi çok iyi
konuşur. XTTS klonundan kat kat doğal.

- Anahtar ``ELEVENLABS_API_KEY`` ORTAM DEĞİŞKENİNDEN (koda gömülmez) ya da config.
- ÇIKIŞ: ``output_format=pcm_24000`` → ham PCM 16-bit 24 kHz mono (decode yok).
- Disk cache: (voice_id, model_id, text) anahtarı → tekrarlar OFFLINE + anında;
  böylece kota korunur (RAG-hit cevaplar bir kez sentezlenir).
- ÇEVRİMİÇİ + KOTALI (paid tier). Festivalde cache ile yönetilir.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.core.errors import TTSError
from src.core.interfaces import TTSProvider
from src.core.logger import get_logger
from src.tts.cache_util import provider_cache_dir, prune_cache

_log = get_logger(component="tts.elevenlabs")

_API = "https://api.elevenlabs.io/v1/text-to-speech"
_CHUNK = 9600  # 0.2 sn @ 24 kHz, 16-bit


class ElevenConfig(BaseModel):
    voice_id: str = ""                        # ElevenLabs ses kimliği (İshak klonu)
    model_id: str = "eleven_multilingual_v2"  # Türkçe için en iyi kalite
    api_key: str | None = None                # yoksa ELEVENLABS_API_KEY env
    stability: float = 0.5
    similarity_boost: float = 0.85
    style: float = 0.0
    use_speaker_boost: bool = True
    cache_enabled: bool = True
    cache_dir: str = "data/tts_cache"
    cache_max_entries: int = 2000   # 28 saatte sınırsız disk büyümesini önler
    timeout_seconds: float = 30.0

    model_config = {"extra": "ignore", "protected_namespaces": ()}


class ElevenLabsCloudTTS(TTSProvider):
    def __init__(self, config: dict[str, Any] | ElevenConfig | None = None) -> None:
        if config is None:
            self.config = ElevenConfig()
        elif isinstance(config, ElevenConfig):
            self.config = config
        else:
            self.config = ElevenConfig(**config)
        # Sağlayıcı izolasyonu: kendi alt-klasörüne yaz (prune çakışması olmasın).
        self._cache = (
            provider_cache_dir(self.config.cache_dir, "eleven")
            if self.config.cache_enabled
            else Path(self.config.cache_dir)
        )

    def _api_key(self) -> str:
        key = self.config.api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise TTSError(
                "TTS_001",
                "ELEVENLABS_API_KEY tanımlı değil. setx ELEVENLABS_API_KEY \"...\" ile ver.",
            )
        return key

    def _cache_path(self, text: str) -> Path | None:
        if not self.config.cache_enabled:
            return None
        sig = f"el|{self.config.voice_id}|{self.config.model_id}|{text}"
        return self._cache / f"{hashlib.sha256(sig.encode()).hexdigest()[:24]}.pcm"

    async def warmup(self, voice_id: str = "cezeri") -> int:
        import time as _t

        start = _t.perf_counter()
        async for _ in self.synthesize_stream("Bir, iki, üç.", voice_id):
            pass
        return int((_t.perf_counter() - start) * 1000)

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        if not self.config.voice_id:
            raise TTSError("TTS_002", "ElevenLabs voice_id boş — config'e İshak ses kimliğini yaz.")

        cpath = self._cache_path(text)
        if cpath is not None and cpath.exists():
            _log.info("tts_cache_hit", key=cpath.stem)
            data = cpath.read_bytes()
            for i in range(0, len(data), _CHUNK):
                yield data[i : i + _CHUNK]
            return

        try:
            import httpx
        except ImportError as exc:
            raise TTSError("TTS_001", "httpx kurulu değil: pip install httpx", cause=exc) from exc

        url = f"{_API}/{self.config.voice_id}/stream"
        params = {"output_format": "pcm_24000"}
        headers = {"xi-api-key": self._api_key(), "Content-Type": "application/json"}
        body = {
            "text": text,
            "model_id": self.config.model_id,
            "voice_settings": {
                "stability": self.config.stability,
                "similarity_boost": self.config.similarity_boost,
                "style": self.config.style,
                "use_speaker_boost": self.config.use_speaker_boost,
            },
        }
        buf = bytearray() if cpath is not None else None
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                async with client.stream(
                    "POST", url, params=params, headers=headers, json=body
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "ignore")[:200]
                        raise TTSError("TTS_001", f"ElevenLabs {resp.status_code}: {detail}")
                    async for chunk in resp.aiter_bytes(_CHUNK):
                        if not chunk:
                            continue
                        if buf is not None:
                            buf.extend(chunk)
                        yield chunk
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "TTS_001", f"ElevenLabs hatası: {type(exc).__name__}: {exc}", cause=exc
            ) from exc

        if buf is not None and cpath is not None and buf:
            try:
                cpath.write_bytes(bytes(buf))
                prune_cache(self._cache, self.config.cache_max_entries)
            except OSError as exc:
                _log.warning("eleven_cache_write_failed", error=str(exc))

    async def close(self) -> None:
        return None


__all__ = ["ElevenLabsCloudTTS", "ElevenConfig"]
