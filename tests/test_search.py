"""
Tests for the Deduplicator.
"""
import pytest

from app.models.result import SearchResult
from app.search.deduplicator import Deduplicator


def _make_result(url: str, score: float = 0.5, title: str = "Test") -> SearchResult:
    from app.utils.url_utils import normalize_url, extract_domain
    return SearchResult(
        url=url,
        title=title,
        snippet="snippet",
        score=score,
        domain=extract_domain(url),
        normalized_url=normalize_url(url),
    )


class TestDeduplicator:
    def setup_method(self):
        self.dedup = Deduplicator()

    def test_removes_duplicates(self):
        results = [
            _make_result("https://example.com/page", score=0.9),
            _make_result("https://example.com/page/", score=0.5),  # trailing slash dupe
            _make_result("https://other.com/post", score=0.7),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 2

    def test_keeps_highest_score(self):
        results = [
            _make_result("https://example.com/page", score=0.5),
            _make_result("https://example.com/page/", score=0.9),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 1
        assert unique[0].score == 0.9

    def test_no_duplicates_unchanged(self):
        results = [
            _make_result("https://a.com/1"),
            _make_result("https://b.com/2"),
            _make_result("https://c.com/3"),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 3

    def test_empty_list(self):
        assert self.dedup.deduplicate([]) == []

    def test_fragment_duplicates(self):
        results = [
            _make_result("https://example.com/page#section1"),
            _make_result("https://example.com/page#section2"),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 1

    def test_utm_param_duplicates(self):
        results = [
            _make_result("https://example.com/page?utm_source=google"),
            _make_result("https://example.com/page?utm_source=twitter"),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 1

    def test_ten_to_seven(self):
        """10 results with duplicates should become fewer unique results."""
        results = [
            _make_result("https://site1.com/page", score=0.9),
            _make_result("https://site1.com/page/", score=0.5),  # dupe
            _make_result("https://site2.com/post", score=0.8),
            _make_result("https://site2.com/post#section", score=0.7),  # dupe
            _make_result("https://site3.com/article?utm_source=x", score=0.6),
            _make_result("https://site3.com/article", score=0.65),  # dupe
            _make_result("https://site4.com/news"),
            _make_result("https://site5.com/about"),
            _make_result("https://site6.com/home"),
            _make_result("https://site7.com/faq"),
        ]
        unique = self.dedup.deduplicate(results)
        assert len(unique) == 7
