"""
Tavily search provider implementation.

Wraps the Tavily API behind the SearchProvider interface.
Handles authentication, timeouts, retries, and result normalization.
"""
from __future__ import annotations

import logging
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models.result import SearchResult
from app.models.search import SearchFreshness
from app.search.base import SearchProvider
from app.utils.url_utils import extract_domain, normalize_url

logger = logging.getLogger(__name__)

# Map our freshness enum to Tavily's time_range parameter
_FRESHNESS_MAP: dict[str, str] = {
    SearchFreshness.day: "day",
    SearchFreshness.week: "week",
    SearchFreshness.month: "month",
    SearchFreshness.year: "year",
}


class TavilySearchProvider(SearchProvider):
    """Searches the web using the Tavily API."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.tavily_api_key
        self._client = None  # Lazy initialization

    def _get_client(self):
        """Lazily initialize the Tavily client."""
        if self._client is None:
            try:
                from tavily import TavilyClient
                self._client = TavilyClient(api_key=self._api_key)
            except ImportError:
                raise RuntimeError(
                    "tavily-python is not installed. "
                    "Run: pip install tavily-python"
                )
        return self._client

    @property
    def name(self) -> str:
        return "Tavily"

    def is_available(self) -> bool:
        return bool(self._api_key)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        max_results: int = 10,
        domains: Optional[list[str]] = None,
        freshness: Optional[SearchFreshness] = None,
        search_depth: str = "basic",
    ) -> list[SearchResult]:
        """Execute search via Tavily API."""
        if not self.is_available():
            logger.error("Tavily API key not configured")
            return []

        client = self._get_client()

        kwargs: dict = {
            "query": query,
            "max_results": min(max_results, 20),  # Tavily cap
            "search_depth": search_depth,
            "include_raw_content": False,
            "include_answer": False,
        }

        if domains:
            kwargs["include_domains"] = domains

        if freshness and freshness in _FRESHNESS_MAP:
            kwargs["time_range"] = _FRESHNESS_MAP[freshness]

        logger.info(
            "Tavily search | query=%r | max_results=%d | depth=%s",
            query, max_results, search_depth,
        )

        try:
            # Tavily client is synchronous; wrap it
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.search(**kwargs),
            )
        except Exception as exc:
            logger.error("Tavily search failed: %s", exc)
            raise

        results: list[SearchResult] = []
        for raw in response.get("results", []):
            url = raw.get("url", "")
            if not url:
                continue
            result = SearchResult(
                title=raw.get("title", ""),
                url=url,
                snippet=raw.get("content", ""),
                content=raw.get("raw_content", "") or raw.get("content", ""),
                score=float(raw.get("score", 0.0)),
                domain=extract_domain(url),
                source_type="web",
                normalized_url=normalize_url(url),
            )
            results.append(result)

        logger.info("Tavily returned %d results for %r", len(results), query)
        return results
