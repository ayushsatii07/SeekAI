"""
FastAPI application entry point.

Start with:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_crawler import router as crawler_router
from app.api.routes_health import router as health_router
from app.api.routes_history import router as history_router
from app.api.routes_search import router as search_router
from app.config import settings
from app.crawler.worker import crawler_worker
from app.database.database import get_db
from app.utils.logging_config import setup_logging

# Configure logging before anything else
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    logger.info("SeekAI starting up…")
    try:
        db = get_db()
        logger.info("Database ready at %s", settings.db_path)
    except Exception as exc:
        logger.error("Database startup failed: %s", exc)

    # Start real-time breaking news & RSS crawler worker
    if settings.crawler_enabled:
        try:
            crawler_worker.start()
            logger.info("Background RSS crawler worker initialized.")
        except Exception as exc:
            logger.error("Failed to start crawler worker: %s", exc)

    yield

    logger.info("SeekAI shutting down…")
    try:
        crawler_worker.stop()
    except Exception as exc:
        logger.debug("Crawler worker stop exception: %s", exc)


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI instance."""
    app = FastAPI(
        title="SeekAI",
        description="AI-Powered Custom Search Engine with grounded answers, breaking news RSS crawler, and multi-tenant rate limiting",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — allow all origins for local development / web frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(history_router)
    app.include_router(auth_router)
    app.include_router(crawler_router)

    # Also mount routers under /api for path-prefix rewrites and proxy compatibility
    app.include_router(health_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    app.include_router(history_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(crawler_router, prefix="/api")

    return app


app = create_app()
