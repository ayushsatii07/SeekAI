"""
Search request / result models.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SearchMode(str, Enum):
    web = "web"
    local = "local"
    hybrid = "hybrid"
    academic = "academic"  # routes exclusively to academic/research providers
    news = "news"  # routes to crawled breaking news & live RSS articles


class SearchFreshness(str, Enum):
    day = "day"
    week = "week"
    month = "month"
    year = "year"


class SearchRequest(BaseModel):
    """Validated search request from CLI or API."""

    query: str = Field(..., min_length=1, max_length=500)
    max_results: int = Field(default=10, ge=1, le=50)
    mode: SearchMode = SearchMode.web
    freshness: Optional[SearchFreshness] = None
    domains: Optional[list[str]] = None
    use_ai: bool = True

    @field_validator("query")
    @classmethod
    def strip_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query cannot be empty or whitespace-only")
        return stripped

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is not None and len(v) > 10:
            raise ValueError("Too many domain filters (max 10)")
        return v
