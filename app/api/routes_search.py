"""
Search API routes (POST /search, POST /ask, and POST /ask/stream).

Supports standard JSON responses and real-time Server-Sent Events (SSE) streaming answers.
Integrates tenant authentication and sliding window rate limiting.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_tenant
from app.auth.models import Tenant
from app.models.answer import AnswerResponse
from app.models.search import SearchFreshness, SearchMode, SearchRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


class SearchRequestBody(BaseModel):
    """API request body for search and ask endpoints."""

    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    max_results: int = Field(default=10, ge=1, le=50)
    mode: SearchMode = SearchMode.web
    freshness: Optional[SearchFreshness] = None
    domains: Optional[list[str]] = None
    stream: bool = Field(default=False, description="Enable Server-Sent Events (SSE) streaming")


def _get_manager():
    """Dependency: return a configured SearchManager."""
    from app.ai.client import AIClient
    from app.database.repository import SearchRepository
    from app.search.search_manager import SearchManager
    from app.search.tavily_provider import TavilySearchProvider

    ai = AIClient()
    provider = TavilySearchProvider()
    repo = SearchRepository()
    return SearchManager(
        search_provider=provider,
        ai_client=ai,
        repository=repo,
    )


async def _sse_stream_generator(manager, request: SearchRequest) -> AsyncGenerator[str, None]:
    """Format manager.stream_search output as standard Server-Sent Events."""
    try:
        async for event_item in manager.stream_search(request):
            event_type = event_item.get("event", "message")
            event_data = event_item.get("data", {})
            yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
    except Exception as exc:
        logger.error("SSE stream error: %s", exc)
        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"


@router.post("/search", response_model=AnswerResponse)
async def search(
    body: SearchRequestBody,
    manager=Depends(_get_manager),
    tenant: Tenant = Depends(get_current_tenant),
) -> AnswerResponse:
    """
    Search web, news, academic, or local documents and return ranked results without AI synthesis.
    """
    request = SearchRequest(
        query=body.query,
        max_results=body.max_results,
        mode=body.mode,
        freshness=body.freshness,
        domains=body.domains,
        use_ai=False,
    )
    try:
        return await manager.search(request)
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        raise HTTPException(status_code=500, detail="Search failed. Check logs.")


@router.post("/ask")
async def ask(
    req: Request,
    body: SearchRequestBody,
    manager=Depends(_get_manager),
    tenant: Tenant = Depends(get_current_tenant),
):
    """
    Search and generate an AI-grounded answer with citations.
    Supports streaming answers via Server-Sent Events (SSE) when:
    - body.stream == True
    - Header Accept: text/event-stream
    """
    request = SearchRequest(
        query=body.query,
        max_results=body.max_results,
        mode=body.mode,
        freshness=body.freshness,
        domains=body.domains,
        use_ai=True,
    )

    accept_header = req.headers.get("accept", "")
    wants_stream = body.stream or "text/event-stream" in accept_header

    if wants_stream:
        return StreamingResponse(
            _sse_stream_generator(manager, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        return await manager.search(request)
    except Exception as exc:
        logger.error("Ask failed: %s", exc)
        raise HTTPException(status_code=500, detail="Ask failed. Check logs.")


@router.post("/ask/stream")
async def ask_stream(
    body: SearchRequestBody,
    manager=Depends(_get_manager),
    tenant: Tenant = Depends(get_current_tenant),
):
    """
    Explicit endpoint for streaming AI search answers via Server-Sent Events (SSE).
    """
    request = SearchRequest(
        query=body.query,
        max_results=body.max_results,
        mode=body.mode,
        freshness=body.freshness,
        domains=body.domains,
        use_ai=True,
    )
    return StreamingResponse(
        _sse_stream_generator(manager, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
