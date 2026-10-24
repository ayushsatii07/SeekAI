"""
Query expander.

Generates 2-4 alternative search queries from the primary query and
QueryAnalysis. This helps surface results that a single query might miss.
"""
from __future__ import annotations

import logging

from app.ai.client import AIClient
from app.config import settings
from app.models.result import QueryAnalysis

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a search query expansion expert.
Given a user's primary search query and its analysis, generate 2-4 
alternative queries that will surface complementary or different perspectives.

Rules:
- Return ONLY a JSON object: {"queries": ["query1", "query2", ...]}
- Each query should use different terminology or framing
- Do NOT simply paraphrase; use synonyms, related concepts, or sub-aspects
- Maximum 4 alternative queries
- Keep each query concise (3-8 words ideal)
- Do NOT include the original query in the list
"""


class QueryExpander:
    """Generates alternative search queries to improve result coverage."""

    def __init__(
        self,
        client: AIClient,
        max_queries: int = 0,
    ) -> None:
        self._client = client
        self._max_queries = max_queries or settings.max_queries_per_expansion

    async def expand(
        self,
        primary_query: str,
        analysis: QueryAnalysis,
    ) -> list[str]:
        """
        Generate alternative queries for broader coverage.

        Returns a list of queries starting with the primary search_query,
        followed by the LLM-generated alternatives.

        Args:
            primary_query: The original user query.
            analysis:      Structured output from QueryAnalyzer.

        Returns:
            List of query strings (primary first, then alternatives).
        """
        # Always start with the analyzer's optimized query
        queries = [analysis.search_query]

        # Add any suggested queries from the analyzer (up to limit)
        if analysis.suggested_queries:
            for sq in analysis.suggested_queries[: self._max_queries - 1]:
                if sq and sq not in queries:
                    queries.append(sq)

        # If we still have room, ask the LLM for more
        if len(queries) < self._max_queries:
            llm_queries = await self._llm_expand(primary_query, analysis)
            for q in llm_queries:
                if q and q not in queries and len(queries) < self._max_queries:
                    queries.append(q)

        logger.info(
            "Query expansion: %d queries generated from %r",
            len(queries), primary_query,
        )
        return queries[: self._max_queries]

    async def _llm_expand(
        self, primary_query: str, analysis: QueryAnalysis
    ) -> list[str]:
        """Ask the LLM to generate additional alternative queries."""
        import json

        user_content = (
            f"Primary query: {primary_query}\n"
            f"Optimized query: {analysis.search_query}\n"
            f"Intent: {analysis.intent}\n"
            f"Domain: {analysis.domain}"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            text = await self._client.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=256,
                response_format={"type": "json_object"},
            )
            data = json.loads(text)
            raw_queries = data.get("queries", [])
            return [q for q in raw_queries if isinstance(q, str) and q.strip()]
        except Exception as exc:
            logger.warning("LLM query expansion failed: %s", exc)
            return []
