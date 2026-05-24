"""MockRAGStore — Phase 1 için boş ya da sabit benzerlik döndüren stub."""

from __future__ import annotations

from src.core.interfaces import RAGResult, RAGStore


class MockRAGStore(RAGStore):
    """RAG store stub. ``empty=True`` ise hiçbir şey döndürmez."""

    def __init__(self, empty: bool = False) -> None:
        self.empty = empty

    async def query(
        self,
        question: str,
        persona_id: str,
        top_k: int = 3,
    ) -> list[RAGResult]:
        if self.empty:
            return []
        # Düşük similarity — orchestrator'ın LLM'e düşmesini sağlar.
        return [
            RAGResult(
                response_text=f"[mock-rag:{persona_id}] {question}",
                similarity=0.42,
                metadata={"source": "mock"},
            )
        ]
