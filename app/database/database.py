"""
Database connection manager for SeekAI.

Provides a thread-safe SQLite connection using the standard library.
All queries use parameterized statements to prevent SQL injection.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from app.config import settings
from app.database.schema import SCHEMA_SQL

logger = logging.getLogger(__name__)


class Database:
    """
    SQLite database manager.

    Usage:
        db = Database()
        db.initialize()          # create tables / run migrations
        conn = db.get_connection()
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or settings.db_path
        self._connection: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        """Create database tables if they don't exist."""
        logger.info("Initializing database at %s", self._path)
        conn = self.get_connection()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            logger.info("Database initialized successfully")
        except sqlite3.Error as exc:
            logger.error("Database initialization failed: %s", exc)
            raise

    def get_connection(self) -> sqlite3.Connection:
        """Return (or lazily create) the SQLite connection."""
        if self._connection is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            logger.debug("SQLite connection opened at %s", self._path)
        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
            logger.debug("Database connection closed")

    def is_connected(self) -> bool:
        """Return True if a connection is open."""
        try:
            self.get_connection().execute("SELECT 1")
            return True
        except Exception:
            return False

    def __enter__(self) -> "Database":
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Singleton instance used throughout the application
_db_instance: Optional[Database] = None


def get_db() -> Database:
    """Return the application-level singleton Database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.initialize()
    return _db_instance
