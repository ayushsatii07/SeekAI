"""
SQLite database schema for SeekAI.

Tables:
    searches  — Every search request
    results   — Individual search results per search
    answers   — LLM-generated answers per search
    sources   — Cited sources per answer
    cache     — Query response cache
    documents — FTS5 virtual table for local full-text search
"""
from __future__ import annotations

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─── Searches ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS searches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query            TEXT    NOT NULL,
    normalized_query TEXT    NOT NULL,
    intent           TEXT    DEFAULT '',
    mode             TEXT    DEFAULT 'web',
    timestamp        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    response_time    REAL    DEFAULT 0.0,
    result_count     INTEGER DEFAULT 0
);

-- ─── Results ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    title           TEXT    DEFAULT '',
    url             TEXT    NOT NULL,
    normalized_url  TEXT    DEFAULT '',
    domain          TEXT    DEFAULT '',
    snippet         TEXT    DEFAULT '',
    content         TEXT    DEFAULT '',
    search_score    REAL    DEFAULT 0.0,
    keyword_score   REAL    DEFAULT 0.0,
    source_quality  REAL    DEFAULT 0.0,
    final_score     REAL    DEFAULT 0.0
);

-- ─── Answers ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS answers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    answer    TEXT    NOT NULL,
    model     TEXT    DEFAULT '',
    timestamp TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─── Sources ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id       INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    title           TEXT    DEFAULT '',
    url             TEXT    NOT NULL,
    domain          TEXT    DEFAULT '',
    citation_number INTEGER NOT NULL,
    relevance_score REAL    DEFAULT 0.0
);

-- ─── Cache ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cache (
    cache_key  TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at TEXT NOT NULL
);

-- ─── FTS5 document store for local full-text search ────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS documents
USING fts5(
    title,
    content,
    url UNINDEXED,
    domain UNINDEXED,
    search_id UNINDEXED,
    tokenize = 'porter ascii'
);

-- ─── Multi-Tenancy & Authentication ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id                     TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    tier                   TEXT DEFAULT 'free',
    rate_limit_per_minute  INTEGER DEFAULT 60,
    is_active              INTEGER DEFAULT 1,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         TEXT DEFAULT '',
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    is_active    INTEGER DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used_at TEXT
);

-- ─── News & RSS Crawler ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rss_feeds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    name            TEXT DEFAULT '',
    category        TEXT DEFAULT 'general',
    is_active       INTEGER DEFAULT 1,
    last_crawled_at TEXT,
    articles_count  INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS news_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id      INTEGER REFERENCES rss_feeds(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    domain       TEXT DEFAULT '',
    summary      TEXT DEFAULT '',
    content      TEXT DEFAULT '',
    published_at TEXT,
    crawled_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS news_documents
USING fts5(
    title,
    summary,
    content,
    url UNINDEXED,
    domain UNINDEXED,
    published_at UNINDEXED,
    tokenize = 'porter ascii'
);

-- ─── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_searches_timestamp ON searches(timestamp);
CREATE INDEX IF NOT EXISTS idx_results_search_id  ON results(search_id);
CREATE INDEX IF NOT EXISTS idx_answers_search_id  ON answers(search_id);
CREATE INDEX IF NOT EXISTS idx_sources_answer_id  ON sources(answer_id);
CREATE INDEX IF NOT EXISTS idx_cache_expires_at   ON cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant    ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_news_published     ON news_articles(published_at);
CREATE INDEX IF NOT EXISTS idx_news_crawled       ON news_articles(crawled_at);
"""
