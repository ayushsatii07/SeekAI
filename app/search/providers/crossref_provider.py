"""
Crossref search provider.

Wraps the Crossref REST API (polite pool) — no key required for reads.
Endpoint: https://api.crossref.org/works

Best practices:
    • Always set User-Agent with mailto so Crossref can contact you if needed.
    • Set CROSSREF_MAILTO=you@example.com in .env for polite-pool access.
    • Polite pool: no strict rate limit but fair-use recommended (~50 req/s).

Docs: https://api.crossref.org/swagger-ui/index.html
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

_BASE_URL = "https://api.crossref.org/works"

# Freshness → published-from filter (YYYY-MM-DD)
_FRESHNESS_MONTHS: dict[str, int] = {
    SearchFreshness.day: 0,      # today — no param shortcut; handled below
    SearchFreshness.week: 0,
    SearchFreshness.month: 1,
    SearchFreshness.year: 12,
}


def _item_to_result(item: dict) -> Optional[SearchResult]:
    """Convert a raw Crossref work dict into a SearchResult."""
    doi = (item.get("DOI") or "").strip()
    url = f"https://doi.org/{doi}" if doi else (item.get("URL") or "").strip()
    if not url:
        return None

    # Title (may be a list)
    titles = item.get("title") or []
    title = titles[0].strip() if titles else ""
    if not title:
        return None

    # Authors
    authors = item.get("author") or []
    author_names: list[str] = []
    for author in authors[:3]:
        given = author.get("given") or ""
        family = author.get("family") or ""
        name = f"{given} {family}".strip()
        if name:
            author_names.append(name)
    if len(authors) > 3:
        author_names.append("et al.")
    byline = ", ".join(author_names)

    # Publisher / journal
    publisher = (item.get("publisher") or "").strip()
    container = (
        ((item.get("container-title") or []) + [None])[0] or ""
    ).strip()

    # Published date
    pub_year = None
    pub_date_parts = (
        (item.get("published-print") or item.get("published-online") or {})
        .get("date-parts") or [[]]
    )
    if pub_date_parts and pub_date_parts[0]:
        pub_year = pub_date_parts[0][0]

    # Abstract (not always present)
    abstract = (item.get("abstract") or "").strip()
    # Strip JATS XML tags that sometimes appear
    import re
    abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    # Citation count (cited-by-count)
    import math
    citations = item.get("is-referenced-by-count") or 0
    score = min(1.0, math.log1p(citations) / math.log1p(10_000))

    # Build snippet
    snippet_meta: list[str] = []
    if byline:
        snippet_meta.append(byline)
    if pub_year:
        snippet_meta.append(str(pub_year))
    if container:
        snippet_meta.append(container)
    elif publisher:
        snippet_meta.append(publisher)
    snippet = " | ".join(snippet_meta)
    if abstract:
        snippet += f" — {abstract[:380]}"
    elif not snippet:
        snippet = f"DOI: {doi}" if doi else url

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        content=abstract or snippet,
        score=round(score, 4),
        domain=extract_domain(url),
        source_type="academic:crossref",
        normalized_url=normalize_url(url),
    )


class CrossrefProvider(SearchProvider):
    """Searches scholarly works via the Crossref REST API (polite pool)."""

    def __init__(self, mailto: Optional[str] = None) -> None:
        self._mailto = mailto or settings.crossref_mailto

    @property
    def name(self) -> str:
        return "Crossref"

    def is_available(self) -> bool:
        return True  # Public API, no key required

    def _build_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": f"SeekAI/1.0 (mailto:{self._mailto})",
        }

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
        """Search Crossref for scholarly publications matching the query."""
        limit = min(max_results, settings.crossref_max_results, 1000)

        params: dict[str, str | int] = {
            "query": query.strip(),
            "rows": limit,
            "sort": "relevance",
            "select": (
                "DOI,URL,title,author,published-print,published-online,"
                "container-title,publisher,abstract,is-referenced-by-count"
            ),
        }

        # Date filter
        if freshness == SearchFreshness.day:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            params["filter"] = f"from-pub-date:{cutoff}"
        elif freshness == SearchFreshness.week:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            params["filter"] = f"from-pub-date:{cutoff}"
        elif freshness == SearchFreshness.month:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            params["filter"] = f"from-pub-date:{cutoff}"
        elif freshness == SearchFreshness.year:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
            params["filter"] = f"from-pub-date:{cutoff}"

        logger.info("Crossref search | query=%r | limit=%d", query, limit)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers=self._build_headers(),
        ) as client:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()

        data = resp.json()
        items = (data.get("message") or {}).get("items") or []

        results: list[SearchResult] = []
        for item in items:
            result = _item_to_result(item)
            if result:
                results.append(result)

        logger.info("Crossref returned %d results for %r", len(results), query)
        return results
