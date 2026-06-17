"""ChromaDB tabanlı RAG store + multilingual embedding.

Her persona kendi koleksiyonunu kullanır (örn. `cezeri_responses`). Soru
gelince persona'nın koleksiyonu sorgulanır, en yakın hazır cevap döner.

PRECISION TASARIMI (alakasız cevapları azaltmak için):
- **Per-example embedding**: bir entry'nin HER soru örneği AYRI vektör olur,
  hepsi aynı cevaba (entry_id) bağlanır. Tek bir ortalanmış vektör yerine en
  yakın gerçek ifadeyle eşleşme → çok daha keskin.
- **Dedup**: aday havuzu (candidate_pool) çekilir, entry_id'ye göre en yüksek
  benzerlik tutulur, top_k benzersiz cevap döner.
- **Reranker (opsiyonel)**: cross-encoder ile adaylar yeniden sıralanır
  (config ile açılır; model indirme gerektirir).

Eşik + margin kararı pipeline'da (``rag.similarity_threshold`` + ``rag.margin``).
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
    # intfloat/multilingual-e5 modellerinin gerektirdiği prefix'ler:
    e5_query_prefix: str = "query: "
    e5_passage_prefix: str = "passage: "
    # Dedup öncesi kaç ham vektör çekilsin (per-example olduğu için bol tut)
    candidate_pool: int = 24
    # Reranker (cross-encoder) — opsiyonel, model indirme gerektirir
    use_reranker: bool = False
    reranker_model: str = "data/models/reranker/bge-reranker-v2-m3"

    model_config = {"extra": "forbid"}


class ChromaRAGStore(RAGStore):
    """ChromaDB persistent store, persona başına ayrı collection."""

    def __init__(self, config: dict[str, Any] | RAGStoreConfig | None = None) -> None:
        self.config = self._normalize(config)
        self._client: ClientAPI | None = None
        self._embedder: Any | None = None
        self._reranker: Any | None = None
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
            if self.config.use_reranker and self._reranker is None:
                self._reranker = await asyncio.to_thread(self._load_reranker)

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
        device = self._effective_device()
        _log.info("loading_embedder", model=model_id, device=device)
        return SentenceTransformer(model_id, device=device)

    def _load_reranker(self) -> Any:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LLMError("LLM_001", "sentence-transformers yok (reranker)", cause=exc) from exc
        device = self._effective_device()
        _log.info("loading_reranker", model=self.config.reranker_model, device=device)
        return CrossEncoder(self.config.reranker_model, device=device)

    def _effective_device(self) -> str:
        device = self.config.embedding_device
        try:
            import torch  # type: ignore[import-not-found]

            if device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
        except ImportError:
            device = "cpu"
        return device

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
        raw = await asyncio.to_thread(
            collection.query,
            query_embeddings=emb,
            n_results=self.config.candidate_pool,
            include=["metadatas", "distances"],
        )
        candidates = self._dedup_by_entry(raw)

        # Opsiyonel reranker — adayları cross-encoder ile yeniden sırala
        if self._reranker is not None and candidates:
            candidates = await asyncio.to_thread(self._rerank, question, candidates)

        return candidates[:top_k]

    async def upsert(
        self,
        persona_id: str,
        entries: list[dict[str, Any]],
    ) -> int:
        """Persona koleksiyonuna hazır cevaplar yükle (per-example vektör).

        Her entry: {"id", "question_examples": [...], "response", "topic", ...}
        Her soru örneği AYRI Chroma kaydı olur (id = "<entry>__<i>"), hepsi aynı
        cevaba bağlanır.
        """
        await self._ensure_ready()
        collection_name = self._collection_name(persona_id)
        collection = self._collections.get(collection_name)
        if collection is None:
            collection = await asyncio.to_thread(
                self._get_collection, collection_name, create=True
            )
            self._collections[collection_name] = collection
        if collection is None:
            raise LLMError("LLM_001", f"RAG koleksiyonu oluşturulamadı: {collection_name}")

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        entry_count = 0

        for entry in entries:
            qid = entry["id"]
            examples = entry.get("question_examples") or []
            if not examples:
                _log.warning("rag_entry_no_examples", entry_id=qid)
                continue
            entry_count += 1
            meta_base = {
                "entry_id": qid,
                "response": entry["response"],
                "topic": entry.get("topic", ""),
                "difficulty": entry.get("difficulty", ""),
                "intent_keywords": ",".join(entry.get("intent_keywords") or []),
                "follow_up_hint": entry.get("follow_up_hint", ""),
            }
            # PER-EXAMPLE: her soru örneği ayrı vektör → aynı cevaba bağlı
            for i, ex in enumerate(examples):
                ids.append(f"{qid}__{i}")
                documents.append(f"{self.config.e5_passage_prefix}{ex}")
                metadatas.append({**meta_base, "match_text": ex})

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
        _log.info("rag_upsert", persona_id=persona_id, entries=entry_count, vectors=len(ids))
        return entry_count

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
        self._collections.clear()
        self._client = None
        self._embedder = None
        self._reranker = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collection_name(self, persona_id: str) -> str:
        return f"{persona_id}_responses"

    def _get_collection(self, name: str, create: bool) -> Any | None:
        assert self._client is not None
        try:
            return self._client.get_collection(name=name)
        except Exception:  # noqa: BLE001 — chromadb NotFoundError varyantları
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

    def _rerank(self, question: str, candidates: list[RAGResult]) -> list[RAGResult]:
        assert self._reranker is not None
        pairs = [[question, str(c.metadata.get("match_text", c.response_text))] for c in candidates]
        scores = self._reranker.predict(pairs)
        reranked = []
        for c, s in zip(candidates, scores, strict=False):
            # rerank skorunu similarity olarak kullan (0-1 normalize sigmoid-ish)
            new = RAGResult(
                response_text=c.response_text,
                similarity=float(s),
                metadata={**c.metadata, "embed_similarity": c.similarity, "rerank_score": float(s)},
            )
            reranked.append(new)
        reranked.sort(key=lambda r: r.similarity, reverse=True)
        return reranked

    def _dedup_by_entry(self, raw: dict[str, Any]) -> list[RAGResult]:
        """Per-example sonuçları entry_id'ye göre tekille (en yüksek benzerlik)."""
        meta_batch = raw.get("metadatas") or [[]]
        dist_batch = raw.get("distances") or [[]]
        metas = meta_batch[0] if meta_batch else []
        dists = dist_batch[0] if dist_batch else []

        best: dict[str, RAGResult] = {}
        for meta, dist in zip(metas, dists, strict=False):
            if not isinstance(meta, dict):
                continue
            response_text = str(meta.get("response", ""))
            if not response_text:
                continue
            entry_id = str(meta.get("entry_id", id(meta)))
            similarity = max(0.0, min(1.0, 1.0 - float(dist)))  # cosine mesafe → benzerlik
            existing = best.get(entry_id)
            if existing is None or similarity > existing.similarity:
                best[entry_id] = RAGResult(
                    response_text=response_text,
                    similarity=similarity,
                    metadata=meta,
                )
        results = list(best.values())
        results.sort(key=lambda r: r.similarity, reverse=True)
        return results


__all__ = ["ChromaRAGStore", "RAGStoreConfig"]
