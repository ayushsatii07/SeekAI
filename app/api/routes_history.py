"""
History and stats API routes.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database.repository import SearchRepository

logger = logging.getLogger(__name__)
router = APIRouter(tags=["history"])


def _get_repo() -> SearchRepository:
    return SearchRepository()


@router.get("/history")
async def get_history(limit: int = 20) -> list[dict]:
    """Return search history ordered by most recent."""
    repo = _get_repo()
    return repo.get_history(limit=limit)


@router.get("/history/{search_id}")
async def get_search_detail(search_id: int) -> dict:
    """Return full detail for a specific search including answer and sources."""
    repo = _get_repo()
    search = repo.get_search_by_id(search_id)
    if not search:
        raise HTTPException(status_code=404, detail=f"Search {search_id} not found")

    answer = repo.get_answer_by_search_id(search_id)
    sources = repo.get_sources_by_search_id(search_id)

    return {
        "search": search,
        "answer": answer,
        "sources": sources,
    }


@router.get("/sources/{search_id}")
async def get_sources(search_id: int) -> list[dict]:
    """Return cited sources for a given search."""
    repo = _get_repo()
    sources = repo.get_sources_by_search_id(search_id)
    if not sources:
        raise HTTPException(status_code=404, detail=f"No sources for search {search_id}")
    return sources


@router.get("/stats")
async def get_stats() -> dict:
    """Return aggregate usage statistics."""
    repo = _get_repo()
    return repo.get_stats()
