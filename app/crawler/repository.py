"""
Repository for RSS feeds and news articles persistence with FTS5 search.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.crawler.models import NewsArticle, RSSFeed
from app.database.database import Database, get_db
from app.utils.url_utils import extract_domain, normalize_url

logger = logging.getLogger(__name__)


class NewsRepository:
    """Manages RSS feeds and scraped articles with SQLite FTS5 search."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db or get_db()

    def ensure_default_feeds(self, default_urls: list[str]) -> None:
        """Seed default curated feeds if none are present."""
        try:
            conn = self._db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT count(*) as cnt FROM rss_feeds")
            count = cur.fetchone()["cnt"]
            if count == 0:
                for url in default_urls:
                    domain = extract_domain(url)
                    name = domain.split(".")[0].title() if domain else "News Feed"
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO rss_feeds (url, name, category, is_active)
                        VALUES (?, ?, 'news', 1)
                        """,
                        (url, name),
                    )
                conn.commit()
                logger.info("Seeded %d default RSS feeds", len(default_urls))
        except Exception as exc:
            logger.debug("Failed seeding default feeds: %s", exc)

    def list_feeds(self, active_only: bool = False) -> list[RSSFeed]:
        """Return list of registered RSS feeds."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        query = "SELECT id, url, name, category, is_active, last_crawled_at, articles_count, created_at FROM rss_feeds"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"
        cur.execute(query)
        rows = cur.fetchall()
        return [
            RSSFeed(
                id=r["id"],
                url=r["url"],
                name=r["name"],
                category=r["category"],
                is_active=bool(r["is_active"]),
                last_crawled_at=r["last_crawled_at"],
                articles_count=r["articles_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def add_feed(self, url: str, name: Optional[str] = None, category: str = "news") -> RSSFeed:
        """Register a new RSS feed."""
        norm_url = url.strip()
        feed_name = name or extract_domain(norm_url).title() or "RSS Feed"
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO rss_feeds (url, name, category, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (norm_url, feed_name, category),
        )
        conn.commit()
        feed_id = cur.lastrowid
        return RSSFeed(
            id=feed_id,
            url=norm_url,
            name=feed_name,
            category=category,
            is_active=True,
            articles_count=0,
        )

    def delete_feed(self, feed_id: int) -> bool:
        """Remove an RSS feed."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM rss_feeds WHERE id = ?", (feed_id,))
        conn.commit()
        return cur.rowcount > 0

    def update_feed_crawled(self, feed_id: int, new_articles_count: int) -> None:
        """Update last crawled timestamp and article count for feed."""
        try:
            conn = self._db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE rss_feeds
                SET last_crawled_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    articles_count = articles_count + ?
                WHERE id = ?
                """,
                (new_articles_count, feed_id),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Failed updating feed crawled status: %s", exc)

    def save_article(
        self,
        title: str,
        url: str,
        domain: str,
        summary: str = "",
        content: str = "",
        published_at: Optional[str] = None,
        feed_id: Optional[int] = None,
    ) -> bool:
        """
        Save article to database and FTS5 index.
        Returns True if inserted (new), False if duplicate URL.
        """
        norm_url = normalize_url(url)
        conn = self._db.get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """
                INSERT INTO news_articles (feed_id, title, url, domain, summary, content, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (feed_id, title, norm_url, domain, summary, content, published_at),
            )
            # Index into FTS5 news_documents
            cur.execute(
                """
                INSERT INTO news_documents (title, summary, content, url, domain, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, summary, content, norm_url, domain, published_at),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Already exists (duplicate URL)
            return False
        except Exception as exc:
            logger.error("Failed saving news article: %s", exc)
            return False

    def list_articles(
        self, limit: int = 20, domain: Optional[str] = None
    ) -> list[NewsArticle]:
        """Fetch latest crawled news articles."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        if domain:
            cur.execute(
                """
                SELECT id, feed_id, title, url, domain, summary, content, published_at, crawled_at
                FROM news_articles
                WHERE domain LIKE ?
                ORDER BY crawled_at DESC, id DESC
                LIMIT ?
                """,
                (f"%{domain}%", limit),
            )
        else:
            cur.execute(
                """
                SELECT id, feed_id, title, url, domain, summary, content, published_at, crawled_at
                FROM news_articles
                ORDER BY crawled_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        return [
            NewsArticle(
                id=r["id"],
                feed_id=r["feed_id"],
                title=r["title"],
                url=r["url"],
                domain=r["domain"],
                summary=r["summary"],
                content=r["content"],
                published_at=r["published_at"],
                crawled_at=r["crawled_at"],
            )
            for r in rows
        ]

    def search_news(self, query: str, limit: int = 15) -> list[NewsArticle]:
        """Full-text search on crawled news articles using FTS5."""
        conn = self._db.get_connection()
        cur = conn.cursor()

        # Sanitize query for FTS5
        tokens = [t for t in query.split() if t.isalnum()]
        if not tokens:
            return self.list_articles(limit=limit)

        fts_query = " OR ".join(tokens)
        try:
            cur.execute(
                """
                SELECT a.id, a.feed_id, a.title, a.url, a.domain, a.summary, a.content, a.published_at, a.crawled_at
                FROM news_documents doc
                JOIN news_articles a ON a.url = doc.url
                WHERE news_documents MATCH ?
                ORDER BY bm25(news_documents) ASC, a.crawled_at DESC
                LIMIT ?
                """,
                (fts_query, limit),
            )
            rows = cur.fetchall()
            return [
                NewsArticle(
                    id=r["id"],
                    feed_id=r["feed_id"],
                    title=r["title"],
                    url=r["url"],
                    domain=r["domain"],
                    summary=r["summary"],
                    content=r["content"],
                    published_at=r["published_at"],
                    crawled_at=r["crawled_at"],
                )
                for r in rows
            ]
        except Exception as exc:
            logger.warning("News FTS5 query failed (%s), falling back to LIKE", exc)
            cur.execute(
                """
                SELECT id, feed_id, title, url, domain, summary, content, published_at, crawled_at
                FROM news_articles
                WHERE title LIKE ? OR summary LIKE ?
                ORDER BY crawled_at DESC
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", limit),
            )
            rows = cur.fetchall()
            return [
                NewsArticle(
                    id=r["id"],
                    feed_id=r["feed_id"],
                    title=r["title"],
                    url=r["url"],
                    domain=r["domain"],
                    summary=r["summary"],
                    content=r["content"],
                    published_at=r["published_at"],
                    crawled_at=r["crawled_at"],
                )
                for r in rows
            ]

    def get_article_count(self) -> int:
        """Count total news articles stored."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) as cnt FROM news_articles")
        return cur.fetchone()["cnt"]
