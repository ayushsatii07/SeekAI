"""
arXiv search provider.

Wraps the arXiv Atom feed API (no key required).
Endpoint: https://export.arxiv.org/api/query

Rate limit: ~ 3 requests/second; burst is fine for search UX.
Docs: https://info.arxiv.org/help/api/user-manual.html
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
from app.utils.url_utils import normalize_url

logger = logging.getLogger(__name__)

_BASE_URL = "https://export.arxiv.org/api/query"

# XML namespaces used in arXiv Atom feeds
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# Map our freshness enum to arXiv submittedDate ranges
_FRESHNESS_DAYS: dict[str, int] = {
    SearchFreshness.day: 1,
    SearchFreshness.week: 7,
    SearchFreshness.month: 30,
    SearchFreshness.year: 365,
}


def _arxiv_id_from_url(url: str) -> str:
    """Extract clean arXiv ID from an abs URL like https://arxiv.org/abs/2301.12345."""
    if "/abs/" in url:
        return url.split("/abs/")[-1].split("v")[0]
    return ""


def _entry_to_result(entry: ET.Element) -> Optional[SearchResult]:
    """Parse one <entry> element into a SearchResult."""
    find = lambda tag: entry.findtext(f"atom:{tag}", namespaces=_NS) or ""

    title = " ".join(find("title").split())  # collapse whitespace
    if not title:
        return None

    # Canonical link → prefer /abs/ URL
    url = ""
    for link in entry.findall("atom:link", _NS):
        href = link.get("href", "")
        if link.get("rel") == "alternate" or "/abs/" in href:
            url = href
            break
    if not url:
        url = find("id").strip()
    if not url:
        return None

    abstract = " ".join(find("summary").split())

    # Authors
    authors = entry.findall("atom:author", _NS)
    author_names = [
        (a.findtext("atom:name", namespaces=_NS) or "").strip()
        for a in authors[:3]
    ]
    if len(authors) > 3:
        author_names.append("et al.")
    byline = ", ".join(n for n in author_names if n)

    # Published date
    published_str = find("published").strip()[:10]  # YYYY-MM-DD
    try:
        pub_date = datetime.strptime(published_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - pub_date).days
        # Recency score: decay to 0 over 5 years (1825 days)
        recency = max(0.0, 1.0 - age_days / 1825)
    except ValueError:
        recency = 0.5

    # arXiv categories
    primary_cat = entry.findtext("arxiv:primary_category", namespaces=_NS)
    if primary_cat is None:
        cat_el = entry.find("{http://arxiv.org/schemas/atom}primary_category")
        primary_cat = (cat_el.get("term") if cat_el is not None else "") or ""
    primary_cat = primary_cat.strip()

    snippet_parts = []
    if byline:
        snippet_parts.append(byline)
    if published_str:
        snippet_parts.append(published_str)
    if primary_cat:
        snippet_parts.append(f"[{primary_cat}]")
    snippet = " | ".join(snippet_parts)
    if abstract:
        snippet += f" — {abstract[:380]}"

    # PDF link
    pdf_url = ""
    for link in entry.findall("atom:link", _NS):
        if link.get("type") == "application/pdf":
            pdf_url = link.get("href", "")
            break

    content_parts = [abstract]
    if pdf_url:
        content_parts.append(f"[PDF] {pdf_url}")
    content = "\n".join(p for p in content_parts if p)

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        content=content,
        score=round(recency * 0.8 + 0.2, 4),  # base 0.2, boost with recency
        domain="arxiv.org",
        source_type="academic:arxiv",
        normalized_url=normalize_url(url),
    )


class ArXivProvider(SearchProvider):
    """Searches preprint papers on arXiv via the public Atom feed API."""

    @property
    def name(self) -> str:
        return "arXiv"

    def is_available(self) -> bool:
        return True  # No API key required

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
        """Search arXiv preprints matching the query."""
        limit = min(max_results, settings.arxiv_max_results, 200)

        # Build arXiv search query; wrap in ti: OR abs: for better recall
        clean_query = query.strip()
        search_query = f"all:{clean_query}"

        # Apply date filter via search_query clause
        if freshness and freshness in _FRESHNESS_DAYS:
            days = _FRESHNESS_DAYS[freshness]
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
            search_query += f" AND submittedDate:[{cutoff}* TO *]"

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        logger.info("arXiv search | query=%r | limit=%d", query, limit)

        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()

        # Parse Atom XML
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.error("arXiv XML parse error: %s", exc)
            return []

        entries = root.findall("atom:entry", _NS)
        results: list[SearchResult] = []
        for entry in entries:
            result = _entry_to_result(entry)
            if result:
                results.append(result)

        logger.info("arXiv returned %d results for %r", len(results), query)
        return results
