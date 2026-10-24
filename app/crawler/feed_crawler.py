"""
RSS and breaking news crawler module.

Fetches and parses RSS/Atom feeds asynchronously using httpx and feedparser.
Cleans summaries and optionally extracts article body content.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.crawler.models import NewsArticle, RSSFeed
from app.utils.text_utils import clean_text, truncate_text
from app.utils.url_utils import extract_domain, normalize_url

logger = logging.getLogger(__name__)

# Standard browser user agent for polite scraping
USER_AGENT = "SeekAI-NewsBot/1.0 (+https://github.com/seekai/newsbot; polite research crawler)"


def _clean_html(html_str: str) -> str:
    """Strip HTML tags and return clean text."""
    if not html_str:
        return ""
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        return clean_text(soup.get_text(separator=" ", strip=True))
    except Exception:
        return clean_text(html_str)


class FeedCrawler:
    """Asynchronous RSS / Breaking News crawler."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout
        self.headers = {"User-Agent": USER_AGENT}

    async def fetch_feed(self, feed: RSSFeed, max_articles: int = 15) -> list[NewsArticle]:
        """
        Fetch and parse a single RSS/Atom feed.

        Returns:
            List of parsed NewsArticle instances.
        """
        logger.info("Fetching RSS feed %s [%s]", feed.name, feed.url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(feed.url, headers=self.headers)
                resp.raise_for_status()
                content = resp.text
        except Exception as exc:
            logger.warning("Failed fetching RSS feed %s: %s", feed.url, exc)
            return []

        # Parse with feedparser in threadpool to avoid blocking event loop
        parsed = await asyncio.to_thread(feedparser.parse, content)
        articles: list[NewsArticle] = []

        for entry in parsed.entries[:max_articles]:
            link = getattr(entry, "link", "") or ""
            title = getattr(entry, "title", "") or ""
            if not link or not title:
                continue

            summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            summary = _clean_html(summary_raw)
            domain = extract_domain(link)
            published = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""

            # Check for full content in entry.content if available
            body = ""
            if hasattr(entry, "content") and entry.content:
                for c in entry.content:
                    if "value" in c:
                        body += " " + _clean_html(c["value"])
            body = body.strip() or summary

            articles.append(
                NewsArticle(
                    feed_id=feed.id,
                    title=clean_text(title),
                    url=normalize_url(link),
                    domain=domain,
                    summary=truncate_text(summary, 600),
                    content=truncate_text(body, 3000),
                    published_at=published,
                )
            )

        logger.info("Parsed %d articles from %s", len(articles), feed.name)
        return articles

    async def crawl_feeds(
        self, feeds: list[RSSFeed], max_articles_per_feed: int = 15
    ) -> list[NewsArticle]:
        """Crawl multiple feeds concurrently."""
        tasks = [
            self.fetch_feed(f, max_articles=max_articles_per_feed)
            for f in feeds
            if f.is_active
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_articles: list[NewsArticle] = []
        for r in results:
            if isinstance(r, list):
                all_articles.extend(r)
            elif isinstance(r, Exception):
                logger.error("Error in feed crawl task: %s", r)
        return all_articles
