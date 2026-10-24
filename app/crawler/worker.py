"""
Background news and RSS scraper worker.

Periodically fetches curated RSS feeds and breaking news domains,
indexes them in SQLite FTS5 and optionally vector storage.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import uuid
from typing import Optional

from app.config import settings
from app.crawler.feed_crawler import FeedCrawler
from app.crawler.models import CrawlerStatus, NewsArticle
from app.crawler.repository import NewsRepository
from app.search.vector.store import ChromaVectorStore, VectorDocument

logger = logging.getLogger(__name__)


class NewsCrawlerWorker:
    """Background worker that continuously crawls registered RSS feeds."""

    def __init__(
        self,
        repository: Optional[NewsRepository] = None,
        crawler: Optional[FeedCrawler] = None,
        vector_store: Optional[ChromaVectorStore] = None,
        interval_seconds: Optional[int] = None,
    ) -> None:
        self._repo = repository or NewsRepository()
        self._crawler = crawler or FeedCrawler(timeout=settings.request_timeout)
        self._vector_store = vector_store
        self._interval = interval_seconds or settings.crawler_interval_seconds

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_run_at: Optional[str] = None
        self._last_crawled_count = 0
        self._last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def vector_store(self) -> Optional[ChromaVectorStore]:
        if self._vector_store is None:
            try:
                self._vector_store = ChromaVectorStore()
            except Exception as exc:
                logger.debug("Vector store not available for news worker: %s", exc)
        return self._vector_store

    def start(self) -> None:
        """Start the background scraper worker task."""
        if self._running or self._task is not None:
            logger.info("News crawler worker already running.")
            return

        # Ensure default curated feeds exist
        self._repo.ensure_default_feeds(settings.crawler_feed_urls)

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="NewsCrawlerWorker")
        logger.info("News crawler worker started (interval=%ds)", self._interval)

    def stop(self) -> None:
        """Gracefully stop the background worker."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        logger.info("News crawler worker stopped.")

    async def _run_loop(self) -> None:
        """Infinite loop sleeping interval_seconds between crawl runs."""
        while self._running:
            try:
                await self.crawl_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unexpected error in crawler worker loop: %s", exc)
                self._last_error = str(exc)

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def crawl_once(self) -> int:
        """
        Execute a single crawl run over all active feeds.
        Returns the number of newly saved articles.
        """
        async with self._lock:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._last_run_at = now_iso
            self._last_error = None

            feeds = self._repo.list_feeds(active_only=True)
            if not feeds:
                logger.info("No active RSS feeds to crawl.")
                self._last_crawled_count = 0
                return 0

            logger.info("Starting crawl cycle for %d active feeds…", len(feeds))
            new_articles: list[NewsArticle] = []

            for feed in feeds:
                try:
                    feed_articles = await self._crawler.fetch_feed(
                        feed, max_articles=settings.crawler_max_articles_per_feed
                    )
                    saved_for_feed = 0
                    for art in feed_articles:
                        inserted = self._repo.save_article(
                            feed_id=feed.id,
                            title=art.title,
                            url=art.url,
                            domain=art.domain,
                            summary=art.summary,
                            content=art.content,
                            published_at=art.published_at,
                        )
                        if inserted:
                            saved_for_feed += 1
                            new_articles.append(art)

                    self._repo.update_feed_crawled(feed.id, saved_for_feed)
                except Exception as exc:
                    logger.warning("Feed crawl error for %s: %s", feed.url, exc)
                    self._last_error = f"{feed.name}: {exc}"

            self._last_crawled_count = len(new_articles)
            logger.info(
                "Crawl cycle finished: %d new articles ingested.", len(new_articles)
            )

            # Index new articles into Chroma vector store if available
            vs = self.vector_store
            if vs and new_articles:
                try:
                    docs = [
                        VectorDocument(
                            id=str(uuid.uuid5(uuid.NAMESPACE_URL, art.url)),
                            content=f"{art.title}\n{art.summary}\n{art.content[:1000]}".strip(),
                            metadata={
                                "title": art.title,
                                "url": art.url,
                                "domain": art.domain,
                                "source_type": "breaking_news",
                                "published_at": art.published_at or "",
                            },
                        )
                        for art in new_articles[:30]
                    ]
                    await vs.add_documents(docs)
                    logger.info("Indexed %d news articles into Chroma vector store", len(docs))
                except Exception as exc:
                    logger.debug("Vector indexing of news articles failed: %s", exc)

            return len(new_articles)

    def get_status(self) -> CrawlerStatus:
        """Get live crawler status."""
        feeds = self._repo.list_feeds(active_only=False)
        active_cnt = sum(1 for f in feeds if f.is_active)
        total_arts = self._repo.get_article_count()

        return CrawlerStatus(
            is_running=self._running,
            total_feeds=len(feeds),
            active_feeds=active_cnt,
            total_articles=total_arts,
            last_run_at=self._last_run_at,
            last_articles_crawled=self._last_crawled_count,
            last_error=self._last_error,
            interval_seconds=self._interval,
        )


# Global singleton worker instance
crawler_worker = NewsCrawlerWorker()
