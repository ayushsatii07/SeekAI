"""
Health check API route.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.database.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """Return system health status."""
    db_ok = False
    try:
        db = get_db()
        db_ok = db.is_connected()
    except Exception:
        db_ok = False

    return {
        "status": "ok",
        "database": "connected" if db_ok else "error",
        "search_provider": "available" if settings.tavily_api_key else "not configured",
        "llm": "configured" if settings.openai_api_key else "not configured",
        "model": settings.openai_model,
    }


@router.get("/")
async def root() -> dict:
    """Root endpoint — basic information."""
    return {
        "name": "SeekAI",
        "description": "AI-Powered Custom Search Engine",
        "version": "0.1.0",
        "docs": "/docs",
    }
