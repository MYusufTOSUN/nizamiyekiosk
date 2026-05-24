"""MockTTS — text uzunluğuna orantılı sahte PCM chunk'ları üretir."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.core.interfaces import TTSProvider

SAMPLE_RATE = 24000
CHUNK_SAMPLES = 480  # 20 ms @ 24 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2  # PCM 16-bit


class MockTTS(TTSProvider):
    """Sessizlik PCM chunk'ları üretir, gerçek bir audio device gerektirmez."""

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        # Çok kabaca: kelime başına 200 ms ses üret
        word_count = max(1, len(text.split()))
        total_ms = int(200 * word_count / max(speed, 0.1))
        chunks = max(1, total_ms // 20)

        silence = b"\x00" * CHUNK_BYTES
        for _ in range(chunks):
            await asyncio.sleep(0.005)
            yield silence
