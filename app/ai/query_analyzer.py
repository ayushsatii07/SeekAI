"""
Query analyzer — converts natural language into structured search instructions.

Uses the LLM with JSON mode to produce a validated QueryAnalysis object.
Never generates answers here; only intent understanding and query rewriting.
"""
from __future__ import annotations

import logging
import time

from app.ai.client import AIClient
from app.models.result import IntentType, QueryAnalysis

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert search query analyst.
Your task is to analyze the user's natural language query and produce
a structured JSON object that helps a search engine retrieve the best results.

You MUST return a valid JSON object with exactly these fields:
{
  "original_query": "<the user's exact query>",
  "search_query": "<optimized search query for web search engines>",
  "intent": "<one of: informational, navigational, transactional, recommendation, comparison, news, troubleshooting, academic, unknown>",
  "freshness_required": <true if the query needs recent/current information, else false>,
  "suggested_queries": ["<alternative query 1>", "<alternative query 2>", ...],
  "domain": "<broad topic domain, e.g., technology, health, finance, science>",
  "is_time_sensitive": <true if query explicitly mentions time-sensitive terms like 'latest', 'today', '2026'>
}

Rules:
- search_query should be concise, keyword-focused (remove filler words)
- suggested_queries: 2-4 alternative phrasings that may surface different results
- intent: choose the single best-fitting intent type
- freshness_required: true for news, current events, recent releases
- Do NOT add any commentary or explanation, only return the JSON object
"""


class QueryAnalyzer:
    """Analyzes queries and returns structured QueryAnalysis objects."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def analyze(self, query: str) -> QueryAnalysis:
        """
        Analyze a natural language query.

        Args:
            query: The user's raw query string.

        Returns:
            QueryAnalysis with search_query, intent, suggested_queries, etc.
        """
        t0 = time.monotonic()
        logger.info("Analyzing query: %r", query)

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        try:
            analysis = await self._client.chat_structured(
                messages=messages,
                response_model=QueryAnalysis,
                temperature=0.1,
            )
        except Exception as exc:
            logger.warning(
                "Query analysis failed (%s). Using fallback analysis.", exc
            )
            analysis = self._fallback_analysis(query)

        elapsed = time.monotonic() - t0
        logger.info(
            "Query analyzed in %.2fs | intent=%s | queries=%d",
            elapsed, analysis.intent, len(analysis.suggested_queries),
        )
        return analysis

    def _fallback_analysis(self, query: str) -> QueryAnalysis:
        """Create a safe fallback when LLM analysis fails."""
        # Simple time-sensitivity detection
        time_words = {"latest", "today", "current", "recent", "now", "2025", "2026"}
        words = set(query.lower().split())
        is_fresh = bool(words & time_words)

        return QueryAnalysis(
            original_query=query,
            search_query=query,
            intent=IntentType.unknown,
            freshness_required=is_fresh,
            suggested_queries=[],
            domain="",
            is_time_sensitive=is_fresh,
        )
