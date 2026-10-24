"""
Database repository — all CRUD operations for SeekAI.

All SQL uses parameterized queries for safety.
Business logic stays in services; this module only handles persistence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.database.database import Database, get_db
from app.models.answer import AnswerResponse, SearchMetadata, Source
from app.models.result import QueryAnalysis, SearchResult
from app.models.search import SearchMode

logger = logging.getLogger(__name__)


class SearchRepository:
    """Handles persistence for searches, results, answers, and sources."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db or get_db()

    def _conn(self) -> sqlite3.Connection:
        return self._db.get_connection()

    # ─── Searches ────────────────────────────────────────────────────────────

    def save_search(
        self,
        query: str,
        analysis: QueryAnalysis,
        result_count: int,
        response_time: float,
        mode: str = "web",
    ) -> int:
        """Insert a search record and return its ID."""
        conn = self._conn()
        normalized = query.lower().strip()
        cur = conn.execute(
            """
            INSERT INTO searches
                (query, normalized_query, intent, mode, response_time, result_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (query, normalized, analysis.intent, mode, response_time, result_count),
        )
        conn.commit()
        search_id = cur.lastrowid
        logger.debug("Saved search id=%d", search_id)
        return search_id

    def save_results(
        self,
        search_id: int,
        results: list[SearchResult],
    ) -> None:
        """Bulk-insert ranked results for a search."""
        conn = self._conn()
        rows = [
            (
                search_id,
                r.title,
                r.url,
                r.normalized_url,
                r.domain,
                r.snippet,
                r.content[:2000],  # store truncated content
                r.score,
                r.keyword_relevance,
                r.source_quality,
                r.final_score,
            )
            for r in results
        ]
        conn.executemany(
            """
            INSERT INTO results
                (search_id, title, url, normalized_url, domain, snippet,
                 content, search_score, keyword_score, source_quality, final_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        # Index into FTS5
        fts_rows = [
            (r.title, r.content[:500] or r.snippet, r.url, r.domain, search_id)
            for r in results
        ]
        try:
            conn.executemany(
                "INSERT INTO documents (title, content, url, domain, search_id) VALUES (?, ?, ?, ?, ?)",
                fts_rows,
            )
            conn.commit()
        except Exception as exc:
            logger.warning("FTS5 index update failed: %s", exc)

    def save_answer(
        self,
        search_id: int,
        answer: str,
        model: str,
        sources: list[Source],
    ) -> int:
        """Insert an answer and its cited sources. Return answer ID."""
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO answers (search_id, answer, model) VALUES (?, ?, ?)",
            (search_id, answer, model),
        )
        conn.commit()
        answer_id = cur.lastrowid

        source_rows = [
            (
                answer_id,
                s.title,
                s.url,
                s.domain,
                s.citation_number,
                s.relevance_score,
            )
            for s in sources
        ]
        conn.executemany(
            """
            INSERT INTO sources
                (answer_id, title, url, domain, citation_number, relevance_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            source_rows,
        )
        conn.commit()
        logger.debug("Saved answer id=%d with %d sources", answer_id, len(sources))
        return answer_id

    # ─── History ─────────────────────────────────────────────────────────────

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return recent searches ordered by timestamp descending."""
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT id, query, timestamp, result_count, response_time, intent, mode
            FROM searches
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_search_by_id(self, search_id: int) -> Optional[dict]:
        """Retrieve a search record by ID."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM searches WHERE id = ?", (search_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_answer_by_search_id(self, search_id: int) -> Optional[dict]:
        """Retrieve the answer for a given search."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM answers WHERE search_id = ? ORDER BY id DESC LIMIT 1",
            (search_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_sources_by_search_id(self, search_id: int) -> list[dict]:
        """Retrieve cited sources for a given search (via answer)."""
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT s.* FROM sources s
            JOIN answers a ON a.id = s.answer_id
            WHERE a.search_id = ?
            ORDER BY s.citation_number
            """,
            (search_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_results_by_search_id(self, search_id: int) -> list[dict]:
        """Retrieve ranked results for a given search."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM results WHERE search_id = ? ORDER BY final_score DESC",
            (search_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Statistics ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregate statistics."""
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
        avg_time = conn.execute(
            "SELECT AVG(response_time) FROM searches"
        ).fetchone()[0] or 0.0
        total_results = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        total_answers = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        return {
            "total_searches": total,
            "total_results": total_results,
            "total_answers": total_answers,
            "average_response_time": round(avg_time, 2),
        }

    # ─── FTS5 Local Search ───────────────────────────────────────────────────

    def local_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search indexed documents using SQLite FTS5."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT title, url, domain, search_id,
                       snippet(documents, 1, '<b>', '</b>', '...', 32) AS excerpt,
                       rank
                FROM documents
                WHERE documents MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as exc:
            logger.error("FTS5 local search failed: %s", exc)
            return []

    # ─── Cache ───────────────────────────────────────────────────────────────

    def _cache_key(self, query: str, params: dict) -> str:
        raw = json.dumps({"q": query, **params}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def cache_get(self, query: str, params: dict) -> Optional[str]:
        """Return cached response JSON if valid, else None."""
        key = self._cache_key(query, params)
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        row = conn.execute(
            "SELECT response FROM cache WHERE cache_key = ? AND expires_at > ?",
            (key, now),
        ).fetchone()
        if row:
            logger.debug("Cache hit for %r", query[:50])
            return row["response"]
        return None

    def cache_set(self, query: str, params: dict, response: str) -> None:
        """Store a response in the cache."""
        key = self._cache_key(query, params)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=settings.cache_ttl)).isoformat()
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO cache (cache_key, response, expires_at)
            VALUES (?, ?, ?)
            """,
            (key, response, expires),
        )
        conn.commit()

    def cache_purge_expired(self) -> int:
        """Remove expired cache entries. Return count removed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        cur = conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
        conn.commit()
        return cur.rowcount
