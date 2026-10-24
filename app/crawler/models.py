"""
Models for RSS feed crawling and news scraping.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class RSSFeed(BaseModel):
    """Registered RSS feed."""

    id: int
    url: str
    name: str = ""
    category: str = "general"
    is_active: bool = True
    last_crawled_at: Optional[str] = None
    articles_count: int = 0
    created_at: Optional[str] = None


class RSSFeedCreate(BaseModel):
    url: str = Field(..., min_length=5, max_length=500)
    name: Optional[str] = None
    category: str = Field(default="news")


class NewsArticle(BaseModel):
    """Scraped news article from breaking RSS feeds."""

    id: Optional[int] = None
    feed_id: Optional[int] = None
    title: str
    url: str
    domain: str
    summary: str = ""
    content: str = ""
    published_at: Optional[str] = None
    crawled_at: Optional[str] = None


class CrawlerStatus(BaseModel):
    """Current health and progress status of the background scraper worker."""

    is_running: bool
    total_feeds: int
    active_feeds: int
    total_articles: int
    last_run_at: Optional[str] = None
    last_articles_crawled: int = 0
    last_error: Optional[str] = None
    interval_seconds: int = 300
