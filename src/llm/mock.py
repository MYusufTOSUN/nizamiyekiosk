"""MockLLM — persona'nın initial_greeting'inden türetilmiş sahte cevap."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.core.interfaces import ConversationContext, LLMProvider, PersonaConfig


class MockLLM(LLMProvider):
    """Phase 1 test için. Sabit/şablon yanıt streaming olarak gönderir."""

    def __init__(self, response_template: str | None = None) -> None:
        self.response_template = response_template

    async def generate_response(
        self,
        question: str,
        persona: PersonaConfig,
        context: ConversationContext | None = None,
    ) -> AsyncIterator[str]:
        if self.response_template:
            text = self.response_template
        else:
            text = (
                f"Aleyküm selam evladım, ben {persona.name}. "
                f"Sorduğun şey ilginç: \"{question}\". "
                "Sana bir hikaye anlatayım mı?"
            )

        # Token-by-token streaming simülasyonu
        for token in text.split(" "):
            await asyncio.sleep(0.01)
            yield token + " "
