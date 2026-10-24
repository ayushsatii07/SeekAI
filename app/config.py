"""
SeekAI Configuration Module.

Manages all application settings via environment variables and pydantic-settings.
Fails fast with useful error messages if required configuration is missing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Required ──────────────────────────────────────────────────────────
    openai_api_key: str = ""
    tavily_api_key: str = ""

    # ── LLM ───────────────────────────────────────────────────────────────
    openai_model: str = "gpt-4o-mini"
    openai_base_url: Optional[str] = None

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = "data/search.db"

    # ── Search ────────────────────────────────────────────────────────────
    max_search_results: int = 30
    max_llm_sources: int = 8
    max_queries_per_expansion: int = 4

    # ── Network ───────────────────────────────────────────────────────────
    request_timeout: int = 15

    # ── Content ───────────────────────────────────────────────────────────
    max_source_chars: int = 12_000

    # ── Cache ─────────────────────────────────────────────────────────────
    cache_ttl: int = 3_600

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Optional: per-expansion query limits ─────────────────────────────
    results_per_query: int = 10

    # ── Vector Search & Embeddings ─────────────────────────────────────────
    embedding_provider: str = "auto"  # "auto", "local", "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    chroma_persist_dir: str = "data/chroma"
    chroma_collection_name: str = "seekai_docs"
    rrf_k: int = 60
    rrf_vector_weight: float = 0.5
    rrf_fts_weight: float = 0.5

    # ── Academic & Research Providers ─────────────────────────────────────
    # Semantic Scholar — free public API; optional key raises rate limits
    semantic_scholar_api_key: str = ""
    semantic_scholar_max_results: int = 10

    # arXiv — fully public, no key required
    arxiv_max_results: int = 10

    # Crossref — polite-pool requires mailto; no key needed for reads
    crossref_mailto: str = "seekai@example.com"
    crossref_max_results: int = 10

    # ── Authentication & Multi-Tenancy ───────────────────────────────────
    auth_enabled: bool = False
    jwt_secret_key: str = "seekai-secret-key-change-me-in-production-12345678"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24  # 24 hours
    admin_api_key: str = "seekai_admin_secret_key"

    # ── Redis & Rate Limiting ─────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 60
    rate_limit_window_seconds: int = 60

    # ── Real-Time News & RSS Crawler ──────────────────────────────────────
    crawler_enabled: bool = True
    crawler_interval_seconds: int = 300  # 5 minutes
    crawler_max_articles_per_feed: int = 15
    crawler_feed_urls: list[str] = [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.npr.org/1001/rss.xml",
        "https://news.ycombinator.com/rss",
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
    ]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("max_search_results")
    @classmethod
    def validate_max_results(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("max_search_results must be between 1 and 100")
        return v

    @field_validator("max_llm_sources")
    @classmethod
    def validate_max_sources(cls, v: int) -> int:
        if not 1 <= v <= 20:
            raise ValueError("max_llm_sources must be between 1 and 20")
        return v

    def validate_required_for_search(self) -> None:
        """Raise a friendly error when API keys are missing."""
        errors: list[str] = []
        if not self.openai_api_key:
            errors.append(
                "OPENAI_API_KEY is missing.\n"
                "  → Add it to your .env file:  OPENAI_API_KEY=sk-..."
            )
        if not self.tavily_api_key:
            errors.append(
                "TAVILY_API_KEY is missing.\n"
                "  → Add it to your .env file:  TAVILY_API_KEY=tvly-..."
            )
        if errors:
            print("\n[ERROR] Required API keys are missing:\n")
            for err in errors:
                print(f"  ✗ {err}")
            print("\nSee .env.example for the full list of variables.\n")
            sys.exit(1)

    @property
    def db_path(self) -> Path:
        """Resolve database file path, creating parent dirs if needed."""
        path = Path(self.database_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def chroma_path(self) -> Path:
        """Resolve chroma directory path, creating parent dirs if needed."""
        path = Path(self.chroma_persist_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


# Singleton — imported everywhere as `from app.config import settings`
settings = Settings()
