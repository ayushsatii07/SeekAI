"""
Tests for Server-Sent Events (SSE) streaming answers on POST /ask.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.api.routes_search import _get_manager
from app.main import app
from app.models.answer import AnswerResponse, SearchMetadata, Source
from app.models.result import QueryAnalysis, SearchResult


class MockSearchManager:
    async def search(self, request):
        return AnswerResponse(
            query=request.query,
            answer="This is a test answer [1].",
            sources=[
                Source(
                    id=1,
                    title="Mock Source",
                    url="https://example.com/source",
                    domain="example.com",
                    citation_number=1,
                    relevance_score=0.95,
                )
            ],
            metadata=SearchMetadata(mode=request.mode),
        )

    async def stream_search(self, request):
        yield {"event": "status", "data": {"stage": "analyzing", "message": "Analyzing query..."}}
        yield {"event": "status", "data": {"stage": "searching", "message": "Searching..."}}
        yield {
            "event": "sources",
            "data": [
                {
                    "id": 1,
                    "title": "Mock Source",
                    "url": "https://example.com/source",
                    "domain": "example.com",
                    "snippet": "Test snippet",
                    "score": 0.95,
                }
            ],
        }
        tokens = ["This ", "is ", "a ", "streamed ", "answer."]
        for t in tokens:
            yield {"event": "token", "data": {"delta": t}}

        yield {
            "event": "done",
            "data": {
                "query": request.query,
                "answer": "This is a streamed answer.",
                "sources": [],
                "metadata": {"mode": request.mode.value},
            },
        }


@pytest.fixture(autouse=True)
def override_manager():
    mock_mgr = MockSearchManager()
    app.dependency_overrides[_get_manager] = lambda: mock_mgr
    yield
    app.dependency_overrides.pop(_get_manager, None)


class TestSSEStreaming:
    def test_ask_non_streaming_returns_json(self):
        client = TestClient(app)
        resp = client.post("/ask", json={"query": "quantum computing"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert "answer" in data
        assert len(data["sources"]) == 1

    def test_ask_streaming_via_flag(self):
        client = TestClient(app)
        resp = client.post("/ask", json={"query": "quantum computing", "stream": True})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        content = resp.text
        assert "event: status" in content
        assert "event: sources" in content
        assert "event: token" in content
        assert "event: done" in content
        assert "streamed answer." in content

    def test_ask_stream_endpoint(self):
        client = TestClient(app)
        resp = client.post("/ask/stream", json={"query": "autonomous AI"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "event: done" in resp.text
