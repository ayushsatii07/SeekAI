"""
Relevance ranker for search results.

Ranking formula:
    final_score = 0.60 * search_score
               + 0.20 * keyword_relevance
               + 0.20 * source_quality

Scores are normalized to [0, 1].
Each component is independently configurable.
"""
from __future__ import annotations

import logging

from app.models.result import SearchResult
from app.utils.text_utils import extract_keywords, keyword_overlap_score

logger = logging.getLogger(__name__)

# ─── Source quality tiers ────────────────────────────────────────────────────
# Score 1.0 = highly authoritative
# Score 0.0 = unknown / low-trust
#
# Organised as a list of (suffix_or_domain, quality_score) tuples.
# Matched greedily top-to-bottom; first match wins.
SOURCE_QUALITY_CONFIG: list[tuple[str, float]] = [
    # Government
    (".gov", 1.0),
    ("cisa.gov", 1.0),
    ("nist.gov", 1.0),
    ("who.int", 1.0),
    ("nasa.gov", 1.0),
    # Academic
    (".edu", 0.95),
    ("ieee.org", 0.95),
    ("acm.org", 0.95),
    ("arxiv.org", 0.90),
    ("pubmed.ncbi.nlm.nih.gov", 0.95),
    ("scholar.google.com", 0.90),
    # Major news & encyclopedic
    ("reuters.com", 0.90),
    ("bbc.com", 0.88),
    ("apnews.com", 0.90),
    ("theguardian.com", 0.85),
    ("nature.com", 0.92),
    ("science.org", 0.92),
    ("wikipedia.org", 0.80),
    # Tech reference
    ("docs.python.org", 0.90),
    ("developer.mozilla.org", 0.90),
    ("stackoverflow.com", 0.80),
    ("github.com", 0.78),
    # Established tech publishers
    ("ibm.com", 0.82),
    ("microsoft.com", 0.82),
    ("google.com", 0.82),
    ("aws.amazon.com", 0.82),
]

# Default quality for unknown domains
_DEFAULT_QUALITY = 0.50


def _get_source_quality(domain: str) -> float:
    """Look up source quality score for a given domain."""
    domain_lower = domain.lower()
    for pattern, score in SOURCE_QUALITY_CONFIG:
        if domain_lower.endswith(pattern) or domain_lower == pattern.lstrip("."):
            return score
    return _DEFAULT_QUALITY


class Ranker:
    """
    Multi-signal ranker for search results.

    Weights:
        search_score_weight:    contribution of provider's own relevance score
        keyword_weight:         contribution of keyword presence in title/snippet
        source_quality_weight:  contribution of domain trustworthiness
    """

    def __init__(
        self,
        search_score_weight: float = 0.60,
        keyword_weight: float = 0.20,
        source_quality_weight: float = 0.20,
    ) -> None:
        total = search_score_weight + keyword_weight + source_quality_weight
        assert abs(total - 1.0) < 1e-6, "Weights must sum to 1.0"
        self.sw = search_score_weight
        self.kw = keyword_weight
        self.sqw = source_quality_weight

    def rank(self, results: list[SearchResult], query: str) -> list[SearchResult]:
        """
        Score and sort results by multi-signal relevance.

        Args:
            results: Deduplicated search results.
            query:   Original or expanded query string for keyword matching.

        Returns:
            Results sorted by final_score descending.
        """
        keywords = extract_keywords(query)

        for result in results:
            # ── Signal 1: Provider score (already 0–1 from Tavily) ──────────
            search_score = max(0.0, min(1.0, result.score))

            # ── Signal 2: Keyword relevance ──────────────────────────────────
            title_kw  = keyword_overlap_score(keywords, result.title)
            snip_kw   = keyword_overlap_score(keywords, result.snippet)
            content_kw = keyword_overlap_score(keywords, result.content[:500])

            # Weighted: title > snippet > content
            kw_score = (
                1.0 * title_kw
                + 0.6 * snip_kw
                + 0.3 * content_kw
            ) / (1.0 + 0.6 + 0.3)
            kw_score = max(0.0, min(1.0, kw_score))

            # ── Signal 3: Source quality ─────────────────────────────────────
            sq_score = _get_source_quality(result.domain)

            # ── Composite score ──────────────────────────────────────────────
            final = (
                self.sw  * search_score
                + self.kw  * kw_score
                + self.sqw * sq_score
            )

            result.keyword_relevance = round(kw_score, 4)
            result.source_quality    = round(sq_score, 4)
            result.final_score       = round(final,    4)

        ranked = sorted(results, key=lambda r: r.final_score, reverse=True)
        logger.info(
            "Ranked %d results | top score=%.4f",
            len(ranked),
            ranked[0].final_score if ranked else 0.0,
        )
        return ranked
