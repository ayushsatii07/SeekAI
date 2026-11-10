"""
Webpage content extractor.

Fetches URLs with httpx and extracts readable text using BeautifulSoup4.
Respects timeouts, handles errors gracefully, and never bypasses auth/CAPTCHAs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models.result import SearchResult
from app.utils.text_utils import clean_text, truncate_text

logger = logging.getLogger(__name__)

# Tags that contain noise rather than content
_NOISE_TAGS = {
    "script", "style", "nav", "header", "footer", "aside", "noscript",
    "iframe", "svg", "form", "button", "input", "select", "textarea",
    "meta", "link", "head",
}

_USER_AGENT = (
    "Mozilla/5.0 (compatible; SeekAI/0.1; +https://github.com/seekai)"
)


class ContentExtractor:
    """Asynchronously fetches and extracts text from web pages."""

    def __init__(self, timeout: int = 0) -> None:
        self.timeout = timeout or settings.request_timeout
        self.max_chars = settings.max_source_chars

    async def extract(self, result: SearchResult) -> SearchResult:
        """
        Fetch the URL and populate result.content with extracted text.
        Returns the result unchanged if extraction fails.
        """
        if result.content and len(result.content) > 200:
            # Already has content (e.g., from Tavily raw_content) – skip fetch
            result.content = truncate_text(
                clean_text(result.content), self.max_chars
            )
            return result

        parsed = await self._fetch_and_parse(result.url)
        if parsed:
            text, img_url = parsed
            if text:
                result.content = text
            if img_url and not result.image_url:
                result.image_url = img_url
        return result

    async def extract_many(
        self,
        results: list[SearchResult],
        concurrency: int = 5,
    ) -> list[SearchResult]:
        """
        Extract content for multiple results concurrently.

        Args:
            results:     List of search results to enrich.
            concurrency: Max simultaneous HTTP requests.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded(r: SearchResult) -> SearchResult:
            async with semaphore:
                return await self.extract(r)

        tasks = [_bounded(r) for r in results]
        return await asyncio.gather(*tasks)

    async def _fetch_and_parse(self, url: str) -> Optional[tuple[str, str]]:
        """Fetch URL and return (extracted text, image_url), or None on failure."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                logger.debug("Skipping non-HTML response from %s", url)
                return None

            return self._parse_html(response.text)

        except httpx.TimeoutException:
            logger.debug("Timeout fetching %s", url)
        except httpx.HTTPStatusError as exc:
            logger.debug("HTTP %d for %s", exc.response.status_code, url)
        except Exception as exc:
            logger.debug("Failed to fetch %s: %s", url, exc)

        return None

    def _parse_html(self, html: str) -> tuple[str, str]:
        """Parse HTML and return clean readable text and preview image URL."""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Extract og:image or twitter:image before removing noise tags
        img_url = ""
        og_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        if og_tag and og_tag.get("content"):
            content_val = og_tag["content"].strip()
            if content_val.startswith("http"):
                img_url = content_val

        # Remove noise elements
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()

        # Try to find the main content area first
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=re.compile(r"content|main|body", re.I))
            or soup.find(class_=re.compile(r"content|article|post|entry", re.I))
            or soup.body
            or soup
        )

        text = main.get_text(separator="\n", strip=True)
        text = clean_text(text)
        text = truncate_text(text, self.max_chars)

        if len(text) < 50:
            return None  # Not worth returning minimal content

        return (text, img_url)
