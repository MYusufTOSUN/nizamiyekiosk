"""Microsoft Edge (Neural) TTS sağlayıcısı — doğal Türkçe erkek ses.

``tr-TR-AhmetNeural`` gerçek nöral Türkçe sestir (XTTS'in İngilizce-ağırlıklı
dahili seslerinden çok daha doğal). ``pitch_hz`` ile tonu düşürüp El-Cezerî için
**tok/heybetli** bir karakter verilir.

- Ücretsiz, API anahtarı GEREKMEZ (``edge-tts`` paketi).
- ÇIKIŞ: PCM 16-bit, 24000 Hz, mono (TTSProvider sözleşmesi). MP3 → soundfile
  (libsndfile) ile çözülür; ffmpeg gerekmez.
- Disk cache: aynı (ses, pitch, rate, metin) tekrar üretmez → RAG-hit cevapları
  ilk seferden sonra OFFLINE + anında.
- ÇEVRİMİÇİ: her YENİ sentez Microsoft'a gider (internet gerekir). Festivalde
  zaten Claude için internet var; tekrarlar cache'ten gelir. Tam offline isteniyorsa
  ``xtts_local`` sağlayıcısına dön.
"""
from __future__ import annotations

import hashlib
import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from src.core.errors import TTSError
from src.core.interfaces import TTSProvider
from src.core.logger import get_logger

_log = get_logger(component="tts.edge")

NATIVE_SR = 24000
_CHUNK = 4800  # 0.2 sn @ 24 kHz


class EdgeConfig(BaseModel):
    voice: str = "tr-TR-AhmetNeural"  # Türkçe nöral erkek ses
    pitch_hz: int = -30               # ton: negatif = daha tok/derin (heybet için)
    rate_pct: int = -7                # konuşma hızı (% , negatif = ağır/vakur)
    volume_pct: int = 0
    cache_enabled: bool = True
    cache_dir: str = "data/tts_cache"

    model_config = {"extra": "ignore", "protected_namespaces": ()}


class EdgeTTS(TTSProvider):
    def __init__(self, config: dict[str, Any] | EdgeConfig | None = None) -> None:
        if config is None:
            self.config = EdgeConfig()
        elif isinstance(config, EdgeConfig):
            self.config = config
        else:
            self.config = EdgeConfig(**config)
        self._cache = Path(self.config.cache_dir)
        if self.config.cache_enabled:
            self._cache.mkdir(parents=True, exist_ok=True)

    def _key(self, text: str) -> str:
        sig = f"edge|{self.config.voice}|{self.config.pitch_hz}|{self.config.rate_pct}|{text}"
        return hashlib.sha256(sig.encode()).hexdigest()[:24]

    def _cache_path(self, text: str) -> Path | None:
        if not self.config.cache_enabled:
            return None
        return self._cache / f"{self._key(text)}.pcm"

    async def warmup(self, voice_id: str = "cezeri") -> int:
        """Bağlantıyı/cache'i ısıt — kısa bir cümle sentezle."""
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

        # 1) cache
        cpath = self._cache_path(text)
        if cpath is not None and cpath.exists():
            _log.info("tts_cache_hit", key=cpath.stem)
            data = cpath.read_bytes()
            for i in range(0, len(data), _CHUNK * 2):
                yield data[i : i + _CHUNK * 2]
            return

        # 2) Edge'den MP3 al
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError(
                "TTS_001", "edge-tts kurulu değil: pip install edge-tts", cause=exc
            ) from exc

        pitch = f"{self.config.pitch_hz:+d}Hz"
        rate = f"{self.config.rate_pct:+d}%"
        volume = f"{self.config.volume_pct:+d}%"
        try:
            comm = edge_tts.Communicate(
                text, self.config.voice, rate=rate, pitch=pitch, volume=volume
            )
            mp3 = bytearray()
            async for ch in comm.stream():
                if ch.get("type") == "audio" and ch.get("data"):
                    mp3.extend(ch["data"])
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "TTS_001", f"Edge TTS hatası: {type(exc).__name__}: {exc}", cause=exc
            ) from exc

        if not mp3:
            return

        # 3) MP3 → PCM16 24kHz mono (soundfile/libsndfile)
        import soundfile as sf

        audio, sr = sf.read(io.BytesIO(bytes(mp3)), dtype="int16", always_2d=False)
        if audio.ndim > 1:
            audio = audio[:, 0]
        if sr != NATIVE_SR:
            # nadiren; basit doğrusal yeniden örnekleme
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * NATIVE_SR / sr)).astype(np.int64)
            audio = audio[idx]
        pcm = audio.astype(np.int16).tobytes()

        # 4) cache yaz + stream et
        if cpath is not None:
            try:
                cpath.write_bytes(pcm)
            except Exception:  # noqa: BLE001
                pass
        for i in range(0, len(pcm), _CHUNK * 2):
            yield pcm[i : i + _CHUNK * 2]

    async def close(self) -> None:
        return None


__all__ = ["EdgeTTS", "EdgeConfig"]
