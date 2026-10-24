"""
Result deduplicator.

Removes duplicate search results based on normalized URLs.
When duplicates exist, keeps the result with the highest search score.
"""
from __future__ import annotations

import logging

from app.models.result import SearchResult
from app.utils.url_utils import normalize_url

logger = logging.getLogger(__name__)


class Deduplicator:
    """Remove duplicate search results, keeping the best-scored copy."""

    def deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """
        Deduplicate results by normalized URL.

        For each group of URLs that normalize to the same string,
        keep the result with the highest `score`.

        Args:
            results: Raw list of search results (may have duplicates).

        Returns:
            List of unique results.
        """
        seen: dict[str, SearchResult] = {}

        for result in results:
            key = result.normalized_url or normalize_url(result.url)
            if not key:
                key = result.url  # fallback

            if key not in seen:
                seen[key] = result
            else:
                # Keep the result with the higher search score
                if result.score > seen[key].score:
                    seen[key] = result

        unique = list(seen.values())
        removed = len(results) - len(unique)
        logger.info(
            "Deduplication: %d → %d results (%d removed)",
            len(results), len(unique), removed,
        )
        return unique
