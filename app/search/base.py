"""
Abstract base class for search providers.

All search providers must implement SearchProvider.
This allows adding new providers (Semantic Scholar, Google, Bing, etc.)
without modifying the rest of the application.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models.result import SearchResult
from app.models.search import SearchFreshness


class SearchProvider(ABC):
    """Abstract interface for external search APIs."""

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 10,
        domains: Optional[list[str]] = None,
        freshness: Optional[SearchFreshness] = None,
        search_depth: str = "basic",
    ) -> list[SearchResult]:
        """
        Execute a search query and return a list of SearchResult objects.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.
            domains: Optional list of domains to restrict results to.
            freshness: Optional freshness filter for time-sensitive queries.
            search_depth: Provider-specific depth hint ("basic" or "advanced").

        Returns:
            List of SearchResult objects ordered by relevance.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is properly configured."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this provider."""
        ...
