"""
Answer, Source, and SearchResponse models.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Source(BaseModel):
    """A cited source in the final answer."""

    id: int
    title: str
    url: str
    domain: str = ""
    citation_number: int
    relevance_score: float = 0.0
    snippet: str = ""
    image_url: str = ""


class SearchMetadata(BaseModel):
    """Metrics about the search pipeline execution."""

    results_retrieved: int = 0
    results_after_deduplication: int = 0
    sources_used: int = 0
    response_time: float = 0.0
    query_analysis_time: float = 0.0
    search_api_time: float = 0.0
    extraction_time: float = 0.0
    llm_time: float = 0.0
    queries_generated: int = 1
    mode: str = "web"


class SearchResponse(BaseModel):
    """Full response from the search pipeline (search-focused)."""

    query: str
    results: list[dict] = Field(default_factory=list)
    metadata: SearchMetadata = Field(default_factory=SearchMetadata)


class AnswerResponse(BaseModel):
    """Full response from the search pipeline (AI answer + citations)."""

    query: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    metadata: SearchMetadata = Field(default_factory=SearchMetadata)
    search_id: Optional[int] = None
