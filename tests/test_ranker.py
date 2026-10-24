"""
Tests for the relevance Ranker.
"""
import pytest

from app.models.result import SearchResult
from app.search.ranker import Ranker, _get_source_quality
from app.utils.url_utils import extract_domain, normalize_url


def _make_result(
    url: str,
    title: str = "",
    snippet: str = "",
    score: float = 0.5,
) -> SearchResult:
    domain = extract_domain(url)
    return SearchResult(
        url=url,
        title=title,
        snippet=snippet,
        score=score,
        domain=domain,
        normalized_url=normalize_url(url),
    )


class TestSourceQuality:
    def test_gov_domain_high_quality(self):
        score = _get_source_quality("nist.gov")
        assert score >= 0.9

    def test_edu_domain_high_quality(self):
        score = _get_source_quality("mit.edu")
        assert score >= 0.9

    def test_unknown_domain_default(self):
        score = _get_source_quality("random-blog.xyz")
        assert score == 0.5

    def test_wikipedia_quality(self):
        score = _get_source_quality("wikipedia.org")
        assert 0.7 <= score <= 0.9


class TestRanker:
    def setup_method(self):
        self.ranker = Ranker()

    def test_results_sorted_by_final_score(self):
        results = [
            _make_result("https://unknown.xyz/page", title="Unrelated", score=0.3),
            _make_result("https://nist.gov/cybersecurity", title="Cybersecurity Guide", score=0.8),
            _make_result("https://example.com/info", title="Some Info", score=0.5),
        ]
        ranked = self.ranker.rank(results, "cybersecurity")
        # nist.gov should rank highest (high search score + high quality)
        assert ranked[0].domain == "nist.gov"

    def test_all_scores_between_0_and_1(self):
        results = [
            _make_result("https://example.com/a", score=0.9),
            _make_result("https://example.com/b", score=0.1),
        ]
        ranked = self.ranker.rank(results, "test query")
        for r in ranked:
            assert 0.0 <= r.final_score <= 1.0

    def test_keyword_match_boosts_score(self):
        """Result with matching keywords in title should rank higher."""
        results = [
            _make_result(
                "https://a.com/p", title="Machine Learning Tutorial", score=0.5
            ),
            _make_result(
                "https://b.com/q", title="Cooking Recipes", score=0.5
            ),
        ]
        ranked = self.ranker.rank(results, "machine learning")
        assert ranked[0].title == "Machine Learning Tutorial"

    def test_empty_results(self):
        ranked = self.ranker.rank([], "query")
        assert ranked == []

    def test_final_score_populated(self):
        results = [_make_result("https://example.com/x", score=0.7)]
        ranked = self.ranker.rank(results, "test")
        assert ranked[0].final_score > 0.0

    def test_weight_sum_must_equal_one(self):
        with pytest.raises(AssertionError):
            Ranker(search_score_weight=0.5, keyword_weight=0.5, source_quality_weight=0.5)
