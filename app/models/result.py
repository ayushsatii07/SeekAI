"""
SearchResult and QueryAnalysis models.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    informational = "informational"
    navigational = "navigational"
    transactional = "transactional"
    recommendation = "recommendation"
    comparison = "comparison"
    news = "news"
    troubleshooting = "troubleshooting"
    academic = "academic"
    unknown = "unknown"


class SearchResult(BaseModel):
    """A single search result from a provider or local store."""

    title: str = ""
    url: str
    snippet: str = ""
    content: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    domain: str = ""
    source_type: str = "web"  # "web" | "local" | "fts5"

    # Ranking signals (populated after ranking)
    keyword_relevance: float = 0.0
    source_quality: float = 0.0
    final_score: float = 0.0

    # Normalized URL for deduplication
    normalized_url: str = ""

    model_config = {"populate_by_name": True}


class QueryAnalysis(BaseModel):
    """Structured output from the query analyzer LLM call."""

    original_query: str
    search_query: str
    intent: IntentType = IntentType.unknown
    freshness_required: bool = False
    suggested_queries: list[str] = Field(default_factory=list)
    domain: str = ""
    is_time_sensitive: bool = False
