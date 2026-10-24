"""
Tests for the FastAPI endpoints.

External APIs (Tavily, OpenAI) are fully mocked.
Tests run without any API keys.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ─── Mock data ───────────────────────────────────────────────────────────────

MOCK_SEARCH_RESULT = {
    "url": "https://example.com/ml",
    "title": "What is Machine Learning?",
    "snippet": "Machine learning is a subset of AI...",
    "score": 0.95,
    "domain": "example.com",
}

MOCK_ANSWER = "Machine learning is a branch of AI that enables systems to learn from data. [1]"

MOCK_SOURCES = [
    {
        "id": 1,
        "title": "What is Machine Learning?",
        "url": "https://example.com/ml",
        "domain": "example.com",
        "citation_number": 1,
        "relevance_score": 0.95,
        "snippet": "Machine learning is a subset of AI...",
    }
]


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_has_status_field(self, client):
        response = client.get("/health")
        data = response.json()
        assert "status" in data

    def test_root_endpoint(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "SeekAI"


class TestSearchEndpoint:
    def test_search_returns_200(self, client):
        """POST /search should return 200 with mocked manager."""
        from app.api.routes_search import _get_manager
        from app.models.answer import AnswerResponse, SearchMetadata

        mock_manager = MagicMock()
        mock_manager.search = AsyncMock(
            return_value=AnswerResponse(
                query="machine learning",
                answer="",
                sources=[],
                metadata=SearchMetadata(results_retrieved=5),
            )
        )
        app.dependency_overrides[_get_manager] = lambda: mock_manager
        try:
            response = client.post(
                "/search",
                json={"query": "machine learning", "max_results": 5},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "machine learning"
        finally:
            app.dependency_overrides.pop(_get_manager, None)

    def test_ask_returns_answer(self, client):
        """POST /ask should return an AI answer."""
        from app.api.routes_search import _get_manager
        from app.models.answer import AnswerResponse, SearchMetadata, Source

        mock_manager = MagicMock()
        mock_manager.search = AsyncMock(
            return_value=AnswerResponse(
                query="What is ML?",
                answer=MOCK_ANSWER,
                sources=[],
                metadata=SearchMetadata(results_retrieved=10, sources_used=3),
            )
        )
        app.dependency_overrides[_get_manager] = lambda: mock_manager
        try:
            response = client.post(
                "/ask",
                json={"query": "What is ML?"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "answer" in data
            assert len(data["answer"]) > 0
        finally:
            app.dependency_overrides.pop(_get_manager, None)

    def test_search_empty_query_rejected(self, client):
        """Empty query should be rejected with 422."""
        response = client.post("/search", json={"query": ""})
        assert response.status_code == 422

    def test_search_query_too_long_rejected(self, client):
        """Query exceeding max length should be rejected."""
        response = client.post("/search", json={"query": "x" * 501})
        assert response.status_code == 422


class TestHistoryEndpoints:
    def test_history_returns_list(self, client):
        response = client.get("/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_stats_returns_dict(self, client):
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_searches" in data

    def test_nonexistent_search_returns_404(self, client):
        response = client.get("/history/99999")
        assert response.status_code == 404
