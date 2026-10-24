"""
Tests for database operations (SearchRepository).

Uses a real in-memory SQLite database — no mocking required.
"""
import pytest
from pathlib import Path

from app.database.database import Database
from app.database.repository import SearchRepository
from app.models.result import IntentType, QueryAnalysis, SearchResult
from app.models.answer import Source
from app.utils.url_utils import normalize_url, extract_domain


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Provide a temporary SQLite database for each test."""
    db_file = tmp_path / "test_search.db"
    db = Database(db_path=db_file)
    db.initialize()
    return db


@pytest.fixture
def repo(tmp_db):
    return SearchRepository(db=tmp_db)


@pytest.fixture
def sample_analysis():
    return QueryAnalysis(
        original_query="machine learning",
        search_query="machine learning basics",
        intent=IntentType.informational,
        freshness_required=False,
        suggested_queries=[],
        domain="technology",
        is_time_sensitive=False,
    )


@pytest.fixture
def sample_results():
    urls = [
        "https://example.com/ml-intro",
        "https://wikipedia.org/wiki/machine-learning",
        "https://stanford.edu/ml-course",
    ]
    results = []
    for i, url in enumerate(urls):
        r = SearchResult(
            url=url,
            title=f"Result {i+1}",
            snippet=f"Snippet {i+1}",
            score=0.9 - (i * 0.1),
            domain=extract_domain(url),
            normalized_url=normalize_url(url),
        )
        r.final_score = 0.85 - (i * 0.1)
        results.append(r)
    return results


class TestSearchRepository:
    def test_save_and_retrieve_search(self, repo, sample_analysis):
        search_id = repo.save_search(
            query="machine learning",
            analysis=sample_analysis,
            result_count=10,
            response_time=2.5,
        )
        assert search_id is not None
        assert search_id > 0

        row = repo.get_search_by_id(search_id)
        assert row is not None
        assert row["query"] == "machine learning"
        assert row["result_count"] == 10

    def test_save_results(self, repo, sample_analysis, sample_results):
        search_id = repo.save_search(
            query="test", analysis=sample_analysis, result_count=3, response_time=1.0
        )
        repo.save_results(search_id, sample_results)

        rows = repo.get_results_by_search_id(search_id)
        assert len(rows) == 3

    def test_save_and_retrieve_answer(self, repo, sample_analysis):
        search_id = repo.save_search(
            query="test", analysis=sample_analysis, result_count=5, response_time=1.0
        )

        sources = [
            Source(
                id=1, title="IBM ML", url="https://ibm.com/ml",
                citation_number=1, relevance_score=0.9,
            )
        ]
        answer_id = repo.save_answer(
            search_id=search_id,
            answer="Machine learning is...",
            model="gpt-4o-mini",
            sources=sources,
        )
        assert answer_id > 0

        answer = repo.get_answer_by_search_id(search_id)
        assert answer is not None
        assert "Machine learning" in answer["answer"]

    def test_get_history(self, repo, sample_analysis):
        for i in range(5):
            repo.save_search(
                query=f"query {i}",
                analysis=sample_analysis,
                result_count=i,
                response_time=float(i),
            )

        history = repo.get_history(limit=3)
        assert len(history) == 3

    def test_get_stats(self, repo, sample_analysis, sample_results):
        repo.save_search(
            query="test", analysis=sample_analysis, result_count=3, response_time=1.5
        )
        stats = repo.get_stats()
        assert stats["total_searches"] >= 1

    def test_fts5_local_search(self, repo, sample_analysis, sample_results):
        search_id = repo.save_search(
            query="machine learning", analysis=sample_analysis,
            result_count=3, response_time=1.0,
        )
        # Give results actual content for FTS indexing
        for r in sample_results:
            r.content = f"machine learning is a subset of artificial intelligence"
        repo.save_results(search_id, sample_results)

        results = repo.local_search("machine learning", limit=10)
        assert len(results) >= 1

    def test_cache_set_and_get(self, repo):
        repo.cache_set("test query", {"max": 10}, '{"answer": "cached"}')
        result = repo.cache_get("test query", {"max": 10})
        assert result == '{"answer": "cached"}'

    def test_cache_miss_returns_none(self, repo):
        result = repo.cache_get("nonexistent query", {})
        assert result is None
