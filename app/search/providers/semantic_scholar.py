"""
Semantic Scholar search provider.

Wraps the Semantic Scholar Academic Graph (S2AG) API v1.
Endpoint: https://api.semanticscholar.org/graph/v1/paper/search

Rate limits (public pool):
    • No key  : 1 request/second
    • With key : 10 requests/second
    Add SEMANTIC_SCHOLAR_API_KEY to .env to increase limits.

Docs: https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper_search
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
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

_BASE_URL = "https://api.semanticscholar.org/graph/v1"

# Fields to request from the S2AG API
_FIELDS = ",".join([
    "paperId",
    "title",
    "abstract",
    "year",
    "citationCount",
    "authors",
    "externalIds",
    "openAccessPdf",
    "url",
])


class SemanticScholarProvider(SearchProvider):
    """Searches academic papers via the Semantic Scholar Graph API."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.semantic_scholar_api_key

    @property
    def name(self) -> str:
        return "Semantic Scholar"

    def is_available(self) -> bool:
        # No API key required for basic access
        return True

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    @staticmethod
    def _paper_to_result(paper: dict) -> Optional[SearchResult]:
        """Convert a raw S2AG paper dict to a SearchResult."""
        paper_id = paper.get("paperId", "")
        title = (paper.get("title") or "").strip()
        if not title:
            return None

        # Prefer the canonical S2AG URL, then DOI, then arXiv
        url = paper.get("url") or ""
        ext_ids = paper.get("externalIds") or {}
        if not url and ext_ids.get("DOI"):
            url = f"https://doi.org/{ext_ids['DOI']}"
        if not url and ext_ids.get("ArXiv"):
            url = f"https://arxiv.org/abs/{ext_ids['ArXiv']}"
        if not url and paper_id:
            url = f"https://www.semanticscholar.org/paper/{paper_id}"
        if not url:
            return None

        # Build readable snippet
        abstract = (paper.get("abstract") or "").strip()
        year = paper.get("year")
        authors = paper.get("authors") or []
        author_names = ", ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            author_names += " et al."
        year_str = f" ({year})" if year else ""
        byline = f"{author_names}{year_str}" if author_names else ""
        snippet = f"{byline} — {abstract[:400]}" if byline and abstract else (abstract[:500] or byline)

        # Open-access PDF link in content
        oa_pdf = (paper.get("openAccessPdf") or {}).get("url") or ""
        content_parts = [abstract]
        if oa_pdf:
            content_parts.append(f"[Open Access PDF] {oa_pdf}")
        content = "\n".join(p for p in content_parts if p)

        # Score: normalise citation count to [0, 1] via log scale
        import math
        citations = paper.get("citationCount") or 0
        score = min(1.0, math.log1p(citations) / math.log1p(10_000))

        return SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            content=content,
            score=round(score, 4),
            domain=extract_domain(url),
            source_type="academic:s2",
            normalized_url=normalize_url(url),
        )

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
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
        """Search Semantic Scholar for academic papers matching the query."""
        limit = min(max_results, settings.semantic_scholar_max_results, 100)

        params: dict[str, str | int] = {
            "query": query.strip(),
            "limit": limit,
            "fields": _FIELDS,
        }

        logger.info("Semantic Scholar search | query=%r | limit=%d", query, limit)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers=self._build_headers(),
        ) as client:
            resp = await client.get(f"{_BASE_URL}/paper/search", params=params)
            resp.raise_for_status()

        data = resp.json()
        papers = data.get("data", [])

        results: list[SearchResult] = []
        for paper in papers:
            result = self._paper_to_result(paper)
            if result:
                results.append(result)

        logger.info("Semantic Scholar returned %d results for %r", len(results), query)
        return results
