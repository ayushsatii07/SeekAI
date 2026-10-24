"""
Embedding service abstraction for SeekAI.

Supports OpenAI dense embeddings (e.g., text-embedding-3-small)
and mock/offline embeddings for test suites and offline environments.
"""
from __future__ import annotations

import abc
import hashlib
import logging
import math
from typing import Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class BaseEmbeddingService(abc.ABC):
    """Abstract interface for generating vector embeddings."""

    @abc.abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Generate embedding vector for a single query text."""
        ...

    @abc.abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of documents."""
        ...


class OpenAIEmbeddingService(BaseEmbeddingService):
    """Generates embeddings using OpenAI embeddings API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self.client = AsyncOpenAI(
            api_key=self.api_key or "sk-dummy-key-for-init",
            base_url=base_url or settings.openai_base_url,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        cleaned = text.strip() or " "
        response = await self.client.embeddings.create(
            input=[cleaned],
            model=self.model,
            dimensions=self.dimensions,
        )
        return response.data[0].embedding

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document texts."""
        if not texts:
            return []

        cleaned_texts = [t.strip() or " " for t in texts]
        # Batch in chunks of 50 to avoid request size limits
        batch_size = 50
        all_embeddings: list[list[float]] = []

        for i in range(0, len(cleaned_texts), batch_size):
            chunk = cleaned_texts[i : i + batch_size]
            response = await self.client.embeddings.create(
                input=chunk,
                model=self.model,
                dimensions=self.dimensions,
            )
            all_embeddings.extend([item.embedding for item in response.data])

        return all_embeddings


class LocalHuggingFaceEmbeddingService(BaseEmbeddingService):
    """
    Local embedding generator using HuggingFace all-MiniLM-L6-v2 (ONNX).

    Runs completely on-device without external API calls or tokens.
    """

    def __init__(self) -> None:
        from chromadb.utils import embedding_functions
        self._fn = embedding_functions.DefaultEmbeddingFunction()

    async def embed_query(self, text: str) -> list[float]:
        cleaned = text.strip() or " "
        res = self._fn([cleaned])
        return [float(x) for x in res[0]]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        cleaned = [t.strip() or " " for t in texts]
        res = self._fn(cleaned)
        return [[float(x) for x in vec] for vec in res]


class MockEmbeddingService(BaseEmbeddingService):
    """
    Deterministic pseudo-embedding generator for offline tests.

    Produces unit-normalized vectors based on SHA256 hashes of input texts.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    def _generate_vector(self, text: str) -> list[float]:
        # Hash text repeatedly to generate deterministic pseudo-random floats
        raw_values: list[float] = []
        seed = text.encode("utf-8")
        step = 0
        while len(raw_values) < self.dimensions:
            h = hashlib.sha256(seed + step.to_bytes(4, "little")).digest()
            for b in h:
                # normalize between -1.0 and 1.0
                raw_values.append((b - 128) / 128.0)
                if len(raw_values) >= self.dimensions:
                    break
            step += 1

        # L2 normalize vector
        norm = math.sqrt(sum(x * x for x in raw_values)) or 1.0
        return [x / norm for x in raw_values]

    async def embed_query(self, text: str) -> list[float]:
        return self._generate_vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._generate_vector(t) for t in texts]


def get_embedding_service(
    api_key: Optional[str] = None,
    mock: bool = False,
) -> BaseEmbeddingService:
    """
    Factory helper to obtain an appropriate embedding service.

    Automatically uses Local HuggingFace embeddings when OpenAI key is absent
    or when using a 3rd party chat provider like Groq that doesn't host embeddings.
    """
    if mock:
        return MockEmbeddingService(dimensions=settings.embedding_dimensions)

    provider = (settings.embedding_provider or "auto").lower()

    if provider == "local":
        return LocalHuggingFaceEmbeddingService()

    key = api_key or settings.openai_api_key
    base_url = settings.openai_base_url or ""

    # If provider is explicitly openai and key is valid
    if provider == "openai" and key and not key.startswith("sk-dummy"):
        return OpenAIEmbeddingService(api_key=key, base_url=base_url or None)

    # Auto-detection:
    # If base_url points to Groq or non-OpenAI host, or key is Groq (gsk_), fallback to local HuggingFace
    if "groq.com" in base_url or key.startswith("gsk_") or not key or key.startswith("sk-dummy"):
        try:
            return LocalHuggingFaceEmbeddingService()
        except Exception as exc:
            logger.warning("Could not initialize Local HuggingFace embeddings (%s), using Mock", exc)
            return MockEmbeddingService(dimensions=settings.embedding_dimensions)

    return OpenAIEmbeddingService(api_key=key, base_url=base_url or None)
