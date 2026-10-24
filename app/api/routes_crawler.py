"""
News Crawler and RSS management routes.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_tenant
from app.auth.models import Tenant
from app.crawler.models import CrawlerStatus, NewsArticle, RSSFeed, RSSFeedCreate
from app.crawler.repository import NewsRepository
from app.crawler.worker import NewsCrawlerWorker, crawler_worker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/crawler", tags=["crawler"])

_news_repo: Optional[NewsRepository] = None


def get_news_repo() -> NewsRepository:
    global _news_repo
    if _news_repo is None:
        _news_repo = NewsRepository()
    return _news_repo


@router.get("/status", response_model=CrawlerStatus)
async def get_crawler_status(
    worker: NewsCrawlerWorker = Depends(lambda: crawler_worker),
    _: Tenant = Depends(get_current_tenant),
) -> CrawlerStatus:
    """Get background crawler worker health and status."""
    return worker.get_status()


@router.post("/run")
async def trigger_crawl_run(
    worker: NewsCrawlerWorker = Depends(lambda: crawler_worker),
    _: Tenant = Depends(get_current_tenant),
):
    """Manually trigger an immediate RSS crawl cycle."""
    count = await worker.crawl_once()
    return {
        "status": "success",
        "message": f"Crawl cycle completed. Ingested {count} new articles.",
        "new_articles_count": count,
    }


@router.get("/feeds", response_model=list[RSSFeed])
async def list_feeds(
    active_only: bool = False,
    repo: NewsRepository = Depends(get_news_repo),
    _: Tenant = Depends(get_current_tenant),
) -> list[RSSFeed]:
    """List registered RSS feeds."""
    return repo.list_feeds(active_only=active_only)


@router.post("/feeds", response_model=RSSFeed)
async def add_feed(
    body: RSSFeedCreate,
    repo: NewsRepository = Depends(get_news_repo),
    _: Tenant = Depends(get_current_tenant),
) -> RSSFeed:
    """Add a new RSS feed for background scraping."""
    try:
        return repo.add_feed(url=body.url, name=body.name, category=body.category)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to add feed (may already exist): {exc}",
        )


@router.delete("/feeds/{feed_id}")
async def delete_feed(
    feed_id: int,
    repo: NewsRepository = Depends(get_news_repo),
    _: Tenant = Depends(get_current_tenant),
):
    """Delete an RSS feed."""
    success = repo.delete_feed(feed_id)
    if not success:
        raise HTTPException(status_code=404, detail="Feed not found")
    return {"message": f"Feed {feed_id} successfully deleted"}


@router.get("/articles", response_model=list[NewsArticle])
async def list_articles(
    query: Optional[str] = Query(None, description="Search query over crawled news"),
    domain: Optional[str] = Query(None, description="Filter by domain"),
    limit: int = Query(default=20, ge=1, le=100),
    repo: NewsRepository = Depends(get_news_repo),
    _: Tenant = Depends(get_current_tenant),
) -> list[NewsArticle]:
    """Retrieve breaking news articles crawled from RSS feeds."""
    if query:
        return repo.search_news(query, limit=limit)
    return repo.list_articles(limit=limit, domain=domain)
