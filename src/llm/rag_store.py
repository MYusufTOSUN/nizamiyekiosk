"""ChromaDB tabanlı RAG store + multilingual embedding.

Her persona kendi koleksiyonunu kullanır (örn. `cezeri_responses`). Soru
gelince persona'nın koleksiyonu sorgulanır, en yakın hazır cevap döner. Eşik
üstünde hit varsa LLM hiç çağrılmaz — anlık, güvenli, deterministik.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.core.errors import LLMError
from src.core.interfaces import RAGResult, RAGStore
from src.core.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from chromadb.api import ClientAPI  # type: ignore[import-not-found]

_log = get_logger(component="llm.rag")

DEFAULT_EMBEDDING_MODEL = "data/models/embeddings/multilingual-e5-large"


class RAGStoreConfig(BaseModel):
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    store_path: str = "data/responses/vector_store"
    embedding_device: str = "cuda"  # "cuda" varsa, yoksa "cpu"
    e5_query_prefix: str = "query: "      # intfloat/multilingual-e5 modellerinin gerektirdiği prefix
    e5_passage_prefix: str = "passage: "

    model_config = {"extra": "ignore"}


class ChromaRAGStore(RAGStore):
    """ChromaDB persistent store, persona başına ayrı collection."""

    def __init__(self, config: dict[str, Any] | RAGStoreConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._client: ClientAPI | None = None
        self._embedder: Any | None = None
        self._collections: dict[str, Any] = {}
        self._load_lock = asyncio.Lock()

    @staticmethod
    def _normalize(config: dict[str, Any] | RAGStoreConfig | None) -> RAGStoreConfig:
        if config is None:
            return RAGStoreConfig()
        if isinstance(config, RAGStoreConfig):
            return config
        return RAGStoreConfig(**config)

    # ------------------------------------------------------------------
    # Bağımlılıkları lazy yükle
    # ------------------------------------------------------------------

    async def _ensure_ready(self) -> None:
        if self._client is not None and self._embedder is not None:
            return
        async with self._load_lock:
            if self._client is None:
                self._client = await asyncio.to_thread(self._make_client)
            if self._embedder is None:
                self._embedder = await asyncio.to_thread(self._load_embedder)

    def _make_client(self) -> ClientAPI:
        try:
            import chromadb  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LLMError(
                "LLM_001",
                "chromadb kurulu değil. README Phase 3 / LLM'e bak.",
                cause=exc,
            ) from exc

        store_dir = Path(self.config.store_path)
        store_dir.mkdir(parents=True, exist_ok=True)
        _log.info("chroma_init", path=str(store_dir))
        return chromadb.PersistentClient(path=str(store_dir))

    def _load_embedder(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LLMError(
                "LLM_001",
                "sentence-transformers kurulu değil. README Phase 3 / LLM'e bak.",
                cause=exc,
            ) from exc

        model_id = self.config.embedding_model
        device = self.config.embedding_device
        # Cuda yoksa cpu'ya düş
        try:
            import torch  # type: ignore[import-not-found]

            if device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
        except ImportError:
            device = "cpu"

        _log.info("loading_embedder", model=model_id, device=device)
        return SentenceTransformer(model_id, device=device)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    async def query(
        self,
        question: str,
        persona_id: str,
        top_k: int = 3,
    ) -> list[RAGResult]:
        await self._ensure_ready()
        collection_name = self._collection_name(persona_id)
        collection = self._collections.get(collection_name)
        if collection is None:
            collection = await asyncio.to_thread(
                self._get_collection, collection_name, create=False
            )
            if collection is None:
                return []
            self._collections[collection_name] = collection

        prefixed = f"{self.config.e5_query_prefix}{question}"
        emb = await asyncio.to_thread(self._embed, [prefixed])
        result = await asyncio.to_thread(
            collection.query,
            query_embeddings=emb,
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        return self._format_results(result)

    async def upsert(
        self,
        persona_id: str,
        entries: list[dict[str, Any]],
    ) -> int:
        """Persona koleksiyonuna hazır cevaplar yükle.

        Her entry: {"id": str, "question_examples": [str, ...], "response": str,
                    "topic": str, "difficulty": str, "intent_keywords": [str, ...]}
        Embedding metni question_examples birleşimi (e5 passage prefix ile).
        """
        await self._ensure_ready()
        collection_name = self._collection_name(persona_id)
        collection = self._collections.get(collection_name)
        if collection is None:
            collection = await asyncio.to_thread(
                self._get_collection, collection_name, create=True
            )
            self._collections[collection_name] = collection

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for entry in entries:
            qid = entry["id"]
            examples = entry.get("question_examples") or []
            if not examples:
                _log.warning("rag_entry_no_examples", entry_id=qid)
                continue
            # E5: passage prefix; tüm soru örneklerini birleştir
            doc_text = " ".join(examples)
            ids.append(qid)
            documents.append(f"{self.config.e5_passage_prefix}{doc_text}")
            metadatas.append(
                {
                    "response": entry["response"],
                    "topic": entry.get("topic", ""),
                    "difficulty": entry.get("difficulty", ""),
                    "intent_keywords": ",".join(entry.get("intent_keywords") or []),
                    "follow_up_hint": entry.get("follow_up_hint", ""),
                }
            )

        if not ids:
            return 0

        embeddings = await asyncio.to_thread(self._embed, documents)
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        _log.info("rag_upsert", persona_id=persona_id, count=len(ids))
        return len(ids)

    async def reset(self, persona_id: str) -> None:
        await self._ensure_ready()
        name = self._collection_name(persona_id)
        assert self._client is not None
        try:
            await asyncio.to_thread(self._client.delete_collection, name)
            self._collections.pop(name, None)
            _log.info("rag_reset", collection=name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("rag_reset_failed", collection=name, error=str(exc))

    async def load_from_json(self, persona_id: str, json_path: str | Path) -> int:
        """`src/llm/responses/<persona>.json` formatından yükle."""
        path = Path(json_path)
        if not path.exists():
            raise LLMError("LLM_001", f"RAG JSON yok: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "responses" not in data or not isinstance(data["responses"], list):
            raise LLMError("LLM_001", f"Geçersiz RAG JSON formatı: {path}")
        return await self.upsert(persona_id, data["responses"])

    async def close(self) -> None:
        # ChromaDB persistent client kendisi flush eder; embedder PyTorch model.
        self._collections.clear()
        self._client = None
        self._embedder = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collection_name(self, persona_id: str) -> str:
        return f"{persona_id}_responses"

    def _get_collection(self, name: str, create: bool) -> Any | None:
        assert self._client is not None
        try:
            return self._client.get_collection(name=name)
        except Exception:  # noqa: BLE001 — chromadb-specific NotFoundError varyantları
            if create:
                return self._client.create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            return None

    def _embed(self, texts: list[str]) -> list[list[float]]:
        assert self._embedder is not None
        vectors = self._embedder.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    @staticmethod
    def _format_results(raw: dict[str, Any]) -> list[RAGResult]:
        docs_batch = raw.get("documents") or [[]]
        meta_batch = raw.get("metadatas") or [[]]
        dist_batch = raw.get("distances") or [[]]
        if not docs_batch:
            return []
        docs = docs_batch[0]
        metas = meta_batch[0]
        dists = dist_batch[0]
        results: list[RAGResult] = []
        for meta, dist in zip(metas, dists, strict=False):
            # Chroma "cosine" mesafe = 1 - cosine_similarity → similarity'ye çevir
            similarity = max(0.0, min(1.0, 1.0 - float(dist)))
            response_text = ""
            if isinstance(meta, dict):
                response_text = str(meta.get("response", ""))
            if not response_text:
                continue
            results.append(
                RAGResult(
                    response_text=response_text,
                    similarity=similarity,
                    metadata=meta if isinstance(meta, dict) else {},
                )
            )
        return results


__all__ = ["ChromaRAGStore", "RAGStoreConfig"]
