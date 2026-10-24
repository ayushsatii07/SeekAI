"""
Vector store implementation using ChromaDB.

Handles persistent embedding storage, similarity search, and collection lifecycle.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from app.search.vector.embeddings import BaseEmbeddingService, get_embedding_service

logger = logging.getLogger(__name__)


@dataclass
class VectorDocument:
    """Represents a document or chunk to be indexed in the vector store."""

    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None


@dataclass
class VectorSearchResult:
    """Result returned from vector similarity search."""

    id: str
    content: str
    metadata: dict[str, Any]
    score: float  # Normalized similarity score [0.0, 1.0]
    distance: float


class ChromaVectorStore:
    """
    Manages dense vector storage and nearest-neighbor search with ChromaDB.
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: Optional[str] = None,
        embedding_service: Optional[BaseEmbeddingService] = None,
    ) -> None:
        self.persist_dir = Path(persist_dir or settings.chroma_persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name or settings.chroma_collection_name
        self.embedding_service = embedding_service or get_embedding_service()

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        """Return total number of indexed vectors."""
        try:
            return self._collection.count()
        except Exception as exc:
            logger.error("Failed to get vector count: %s", exc)
            return 0

    async def add_documents(
        self,
        documents: list[VectorDocument],
    ) -> list[str]:
        """
        Embed and insert documents into the vector store.

        Returns:
            List of document IDs added.
        """
        if not documents:
            return []

        # Generate embeddings if not already provided
        texts_to_embed: list[str] = []
        indices_to_embed: list[int] = []

        for idx, doc in enumerate(documents):
            if doc.embedding is None:
                texts_to_embed.append(doc.content)
                indices_to_embed.append(idx)

        if texts_to_embed:
            try:
                embeddings = await self.embedding_service.embed_documents(texts_to_embed)
                for i, emb in zip(indices_to_embed, embeddings):
                    documents[i].embedding = emb
            except Exception as exc:
                logger.error("Failed to generate embeddings for documents: %s", exc)
                raise

        ids: list[str] = []
        embeddings_list: list[list[float]] = []
        contents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for doc in documents:
            if doc.embedding is None:
                continue
            ids.append(doc.id)
            embeddings_list.append(doc.embedding)
            contents.append(doc.content)
            # Ensure all metadata values are JSON/Chroma compatible (str, int, float, bool)
            safe_meta = {
                k: v if isinstance(v, (str, int, float, bool)) else str(v)
                for k, v in doc.metadata.items()
            }
            if not safe_meta:
                safe_meta = {"doc_id": doc.id}
            metadatas.append(safe_meta)

        if ids:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings_list,
                documents=contents,
                metadatas=metadatas,
            )
            logger.info("Upserted %d vector documents into %r", len(ids), self.collection_name)

        return ids

    async def search(
        self,
        query: str,
        limit: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchResult]:
        """
        Search for documents semantically similar to query string.
        """
        if not query.strip():
            return []

        query_vector = await self.embedding_service.embed_query(query)
        return self.search_by_vector(query_vector=query_vector, limit=limit, where=where)

    def search_by_vector(
        self,
        query_vector: list[float],
        limit: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchResult]:
        """
        Search for documents by precomputed embedding vector.
        """
        total = self.count()
        if total == 0:
            return []

        fetch_limit = min(limit, total)
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_vector],
            "n_results": fetch_limit,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception as exc:
            logger.error("Chroma vector search query failed: %s", exc)
            return []

        output: list[VectorSearchResult] = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return output

        ids = results["ids"][0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc_id, doc_content, meta, dist in zip(ids, docs, metas, distances):
            # For cosine distance, distance = 1 - cosine_similarity (range [0, 2])
            # Normalize score to [0.0, 1.0]
            similarity = max(0.0, min(1.0, 1.0 - (dist / 2.0)))
            output.append(
                VectorSearchResult(
                    id=doc_id,
                    content=doc_content or "",
                    metadata=meta or {},
                    score=round(similarity, 4),
                    distance=round(dist, 4),
                )
            )

        return output

    def delete(self, ids: list[str]) -> None:
        """Delete documents by IDs."""
        if ids:
            self._collection.delete(ids=ids)

    def clear(self) -> None:
        """Clear all documents from the collection."""
        try:
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.warning("Failed to reset collection: %s", exc)
