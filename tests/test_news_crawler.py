"""
Tests for Real-time News & Breaking RSS Crawling.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.crawler.feed_crawler import FeedCrawler
from app.crawler.models import NewsArticle, RSSFeed
from app.crawler.repository import NewsRepository
from app.crawler.worker import NewsCrawlerWorker
from app.database.database import Database
from app.main import app


SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Tech News Wire</title>
    <link>https://technews.example.com</link>
    <description>Latest breaking technology news</description>
    <item>
      <title>Autonomous AI Agents Set New Benchmark in Coding</title>
      <link>https://technews.example.com/ai-agents-benchmark</link>
      <description>Researchers announced a massive breakthrough in multi-agent orchestration.</description>
      <pubDate>Fri, 24 Oct 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Next-Gen Quantum Processors Enter Commercial Pilot</title>
      <link>https://technews.example.com/quantum-pilot</link>
      <description>Commercial deployment of fault-tolerant qubits begins across 5 data centers.</description>
      <pubDate>Fri, 24 Oct 2026 11:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_crawler.db"
    db = Database(db_path=db_file)
    db.initialize()
    return db


@pytest.fixture
def news_repo(test_db):
    return NewsRepository(db=test_db)


class TestNewsRepository:
    def test_add_and_list_feeds(self, news_repo):
        feed = news_repo.add_feed(url="https://feeds.example.com/rss", name="Example Feed")
        assert feed.id is not None
        assert feed.url == "https://feeds.example.com/rss"
        assert feed.name == "Example Feed"

        all_feeds = news_repo.list_feeds()
        assert len(all_feeds) == 1
        assert all_feeds[0].url == "https://feeds.example.com/rss"

    def test_save_and_search_articles(self, news_repo):
        saved = news_repo.save_article(
            title="Superconductor Discovered at Room Temperature",
            url="https://news.example.com/superconductor",
            domain="news.example.com",
            summary="Scientists confirm high-temperature superconducting material.",
            content="Full report details reproducible results across three independent labs.",
            published_at="2026-10-24T10:00:00Z",
        )
        assert saved is True

        # Duplicate insert returns False
        dup = news_repo.save_article(
            title="Superconductor Discovered at Room Temperature",
            url="https://news.example.com/superconductor",
            domain="news.example.com",
        )
        assert dup is False

        # Search news via FTS5
        hits = news_repo.search_news("Superconductor", limit=5)
        assert len(hits) == 1
        assert "Superconductor" in hits[0].title


class TestFeedCrawler:
    @pytest.mark.asyncio
    async def test_fetch_feed_parses_entries(self):
        crawler = FeedCrawler()
        feed = RSSFeed(id=1, url="https://technews.example.com/feed", name="Tech News")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_RSS_XML
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            articles = await crawler.fetch_feed(feed)
            assert len(articles) == 2
            assert articles[0].title == "Autonomous AI Agents Set New Benchmark in Coding"
            assert "technews.example.com" in articles[0].domain


class TestNewsCrawlerWorker:
    @pytest.mark.asyncio
    async def test_crawl_once_saves_to_repo(self, news_repo):
        feed = news_repo.add_feed(url="https://technews.example.com/feed", name="Tech News")

        crawler = FeedCrawler()
        worker = NewsCrawlerWorker(repository=news_repo, crawler=crawler)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_RSS_XML
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            new_count = await worker.crawl_once()
            assert new_count == 2
            assert news_repo.get_article_count() == 2

            status = worker.get_status()
            assert status.total_articles == 2
            assert status.last_articles_crawled == 2


class TestCrawlerAPI:
    def test_crawler_api_routes(self):
        client = TestClient(app)

        # GET /crawler/status
        status_resp = client.get("/crawler/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert "total_feeds" in data
        assert "total_articles" in data

        # GET /crawler/feeds
        feeds_resp = client.get("/crawler/feeds")
        assert feeds_resp.status_code == 200

        # POST /crawler/feeds
        add_resp = client.post(
            "/crawler/feeds",
            json={"url": "https://newfeed.example.com/rss.xml", "name": "Custom Feed"},
        )
        assert add_resp.status_code == 200
        feed_id = add_resp.json()["id"]

        # GET /crawler/articles
        art_resp = client.get("/crawler/articles")
        assert art_resp.status_code == 200

        # DELETE /crawler/feeds/{id}
        del_resp = client.delete(f"/crawler/feeds/{feed_id}")
        assert del_resp.status_code == 200
