"""MockSTT — gerçek audio'yu yok sayar, sahte transcription döndürür."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.core.interfaces import STTProvider, TranscriptionResult


class MockSTT(STTProvider):
    """Phase 1 test için. Audio chunk'larını tüketir, sabit Türkçe metin verir."""

    def __init__(self, mock_text: str = "Cezeri ile konuşmak istiyorum") -> None:
        self.mock_text = mock_text

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "tr",
    ) -> AsyncIterator[TranscriptionResult]:
        bytes_seen = 0
        async for chunk in audio_stream:
            bytes_seen += len(chunk)
            if bytes_seen <= 0:
                continue

        # Gerçek STT'yi simüle et
        await asyncio.sleep(0.05)
        yield TranscriptionResult(
            text=self.mock_text,
            is_final=True,
            confidence=0.95,
            duration_ms=1500,
        )
