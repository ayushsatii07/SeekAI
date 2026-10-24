"""
Tests for the Query Analyzer (mocked LLM).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.query_analyzer import QueryAnalyzer
from app.models.result import IntentType, QueryAnalysis


@pytest.fixture
def mock_ai_client():
    client = MagicMock()
    client.chat_structured = AsyncMock()
    return client


@pytest.fixture
def analyzer(mock_ai_client):
    return QueryAnalyzer(client=mock_ai_client)


class TestQueryAnalyzer:
    @pytest.mark.asyncio
    async def test_returns_query_analysis(self, analyzer, mock_ai_client):
        """Should return a valid QueryAnalysis from LLM response."""
        expected = QueryAnalysis(
            original_query="What is TCP?",
            search_query="TCP protocol explanation",
            intent=IntentType.informational,
            freshness_required=False,
            suggested_queries=["TCP protocol basics", "Transmission Control Protocol"],
            domain="networking",
            is_time_sensitive=False,
        )
        mock_ai_client.chat_structured.return_value = expected

        result = await analyzer.analyze("What is TCP?")

        assert isinstance(result, QueryAnalysis)
        assert result.intent == IntentType.informational
        assert result.search_query == "TCP protocol explanation"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, analyzer, mock_ai_client):
        """Should return a safe fallback when the LLM call fails."""
        mock_ai_client.chat_structured.side_effect = Exception("API error")

        result = await analyzer.analyze("What is Python?")

        assert isinstance(result, QueryAnalysis)
        assert result.original_query == "What is Python?"
        assert result.intent == IntentType.unknown

    @pytest.mark.asyncio
    async def test_time_sensitive_detection(self, analyzer, mock_ai_client):
        """Fallback should detect time-sensitive queries."""
        mock_ai_client.chat_structured.side_effect = Exception("API error")

        result = await analyzer.analyze("latest AI news 2026")

        assert result.is_time_sensitive or result.freshness_required

    @pytest.mark.asyncio
    async def test_suggested_queries_list(self, analyzer, mock_ai_client):
        """suggested_queries must be a list."""
        mock_ai_client.chat_structured.return_value = QueryAnalysis(
            original_query="React vs Angular",
            search_query="React Angular comparison",
            intent=IntentType.comparison,
            suggested_queries=["React vs Vue vs Angular", "frontend frameworks 2026"],
        )

        result = await analyzer.analyze("React vs Angular")
        assert isinstance(result.suggested_queries, list)
