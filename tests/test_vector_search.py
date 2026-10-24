"""
Unit and integration tests for Semantic Vector Search and Hybrid RRF.
"""
from __future__ import annotations

import tempfile
import pytest

from app.models.result import SearchResult
from app.search.vector.embeddings import (
    MockEmbeddingService,
    OpenAIEmbeddingService,
    get_embedding_service,
)
from app.search.vector.hybrid_rrf import reciprocal_rank_fusion
from app.search.vector.store import (
    ChromaVectorStore,
    VectorDocument,
    VectorSearchResult,
)


class TestEmbeddings:
    @pytest.mark.asyncio
    async def test_mock_embedding_dimensions(self):
        service = MockEmbeddingService(dimensions=128)
        vec = await service.embed_query("quantum computing algorithms")
        assert len(vec) == 128
        # Ensure it is normalized (magnitude ≈ 1.0)
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-4

    @pytest.mark.asyncio
    async def test_mock_embedding_deterministic(self):
        service = MockEmbeddingService(dimensions=64)
        v1 = await service.embed_query("machine learning")
        v2 = await service.embed_query("machine learning")
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_mock_embedding_batch(self):
        service = MockEmbeddingService(dimensions=32)
        docs = ["first document", "second document"]
        batch = await service.embed_documents(docs)
        assert len(batch) == 2
        assert len(batch[0]) == 32
        assert len(batch[1]) == 32

    def test_get_embedding_service_fallback(self):
        service = get_embedding_service(api_key="", mock=True)
        assert isinstance(service, MockEmbeddingService)


class TestChromaVectorStore:
    @pytest.fixture
    def temp_store(self, tmp_path):
        embedder = MockEmbeddingService(dimensions=64)
        store = ChromaVectorStore(
            persist_dir=str(tmp_path / "chroma"),
            collection_name="test_collection",
            embedding_service=embedder,
        )
        return store

    @pytest.mark.asyncio
    async def test_add_and_count_documents(self, temp_store):
        docs = [
            VectorDocument(
                id="doc-1",
                content="Deep learning architectures and transformers",
                metadata={"title": "Transformers Guide", "url": "https://example.com/transformers"},
            ),
            VectorDocument(
                id="doc-2",
                content="Photosynthesis in marine phytoplankton",
                metadata={"title": "Marine Biology", "url": "https://example.com/marine"},
            ),
        ]
        ids = await temp_store.add_documents(docs)
        assert ids == ["doc-1", "doc-2"]
        assert temp_store.count() == 2

    @pytest.mark.asyncio
    async def test_semantic_search(self, temp_store):
        docs = [
            VectorDocument(
                id="ai-1",
                content="Artificial intelligence and large language models",
                metadata={"title": "AI Overview", "url": "https://example.com/ai"},
            ),
            VectorDocument(
                id="cooking-1",
                content="Baking sourdough bread with wild yeast starter",
                metadata={"title": "Sourdough Recipe", "url": "https://example.com/bread"},
            ),
        ]
        await temp_store.add_documents(docs)

        results = await temp_store.search("language models", limit=2)
        assert len(results) > 0
        assert isinstance(results[0], VectorSearchResult)
        assert 0.0 <= results[0].score <= 1.0
        assert "url" in results[0].metadata

    @pytest.mark.asyncio
    async def test_clear_collection(self, temp_store):
        await temp_store.add_documents([
            VectorDocument(id="1", content="Sample doc", metadata={})
        ])
        assert temp_store.count() == 1
        temp_store.clear()
        assert temp_store.count() == 0


class TestHybridRRF:
    def test_empty_inputs_returns_empty(self):
        res = reciprocal_rank_fusion([], [])
        assert res == []

    def test_single_sparse_list(self):
        sparse = [
            SearchResult(title="FTS Match 1", url="https://example.com/1", snippet="text 1", score=0.9),
            SearchResult(title="FTS Match 2", url="https://example.com/2", snippet="text 2", score=0.8),
        ]
        fused = reciprocal_rank_fusion(sparse, [])
        assert len(fused) == 2
        assert fused[0].url == "https://example.com/1"
        assert fused[0].source_type == "local_fts"

    def test_overlapping_sparse_and_dense_boosts_rank(self):
        # Doc 1 is top in sparse, doc 2 is top in dense
        # Doc 3 is present in BOTH sparse (rank 2) and dense (rank 2) -> should receive highest combined RRF score!
        doc1 = SearchResult(title="Doc 1", url="https://example.com/1", snippet="s1", content="c1", score=0.9)
        doc2 = SearchResult(title="Doc 2", url="https://example.com/2", snippet="s2", content="c2", score=0.9)
        doc3 = SearchResult(title="Doc 3", url="https://example.com/3", snippet="s3", content="c3", score=0.7)

        sparse = [doc1, doc3]
        dense = [doc2, doc3]

        # With k=60, w_sparse=0.5, w_dense=0.5:
        # doc1: 0.5 / (60 + 1) = 0.5 / 61 ≈ 0.008196
        # doc2: 0.5 / (60 + 1) = 0.5 / 61 ≈ 0.008196
        # doc3: 0.5 / (60 + 2) + 0.5 / (60 + 2) = 1.0 / 62 ≈ 0.016129 -> higher than doc1 and doc2!
        fused = reciprocal_rank_fusion(sparse, dense, k=60, sparse_weight=0.5, dense_weight=0.5)
        assert len(fused) == 3
        assert fused[0].url == "https://example.com/3"
        assert fused[0].source_type == "hybrid"
        assert fused[0].final_score > fused[1].final_score

    def test_respects_custom_weights(self):
        doc_sparse = SearchResult(title="Sparse Best", url="https://example.com/sparse", score=0.9)
        doc_dense = SearchResult(title="Dense Best", url="https://example.com/dense", score=0.9)

        # Heavily weight dense search (e.g. 0.9 vs 0.1)
        fused = reciprocal_rank_fusion(
            [doc_sparse],
            [doc_dense],
            k=60,
            sparse_weight=0.1,
            dense_weight=0.9,
        )
        assert fused[0].url == "https://example.com/dense"
