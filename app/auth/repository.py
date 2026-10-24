"""
Repository for tenants and API keys persistence in SQLite.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Optional

from app.auth.models import APIKey, Tenant
from app.auth.security import generate_api_key
from app.database.database import Database, get_db

logger = logging.getLogger(__name__)


class AuthRepository:
    """Manages CRUD for tenants and API keys."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db or get_db()
        self._ensure_default_tenant()

    def _ensure_default_tenant(self) -> None:
        """Seed the default tenant if not already present."""
        try:
            conn = self._db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM tenants WHERE id = 'default'")
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO tenants (id, name, tier, rate_limit_per_minute, is_active)
                    VALUES ('default', 'Default Organization', 'pro', 120, 1)
                    """
                )
                conn.commit()
                logger.info("Default tenant created.")
        except Exception as exc:
            logger.debug("Ensure default tenant exception: %s", exc)

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        tier: str = "free",
        rate_limit_per_minute: int = 60,
    ) -> Tenant:
        """Create a new tenant."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tenants (id, name, tier, rate_limit_per_minute, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (tenant_id, name, tier, rate_limit_per_minute),
        )
        conn.commit()
        return Tenant(
            id=tenant_id,
            name=name,
            tier=tier,
            rate_limit_per_minute=rate_limit_per_minute,
            is_active=True,
        )

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Fetch tenant by ID."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, tier, rate_limit_per_minute, is_active, created_at FROM tenants WHERE id = ?",
            (tenant_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return Tenant(
            id=row["id"],
            name=row["name"],
            tier=row["tier"],
            rate_limit_per_minute=row["rate_limit_per_minute"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def list_tenants(self) -> list[Tenant]:
        """List all active tenants."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, tier, rate_limit_per_minute, is_active, created_at FROM tenants ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        return [
            Tenant(
                id=r["id"],
                name=r["name"],
                tier=r["tier"],
                rate_limit_per_minute=r["rate_limit_per_minute"],
                is_active=bool(r["is_active"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def create_api_key(
        self,
        tenant_id: str,
        name: str = "Default Key",
    ) -> tuple[APIKey, str]:
        """
        Generate and persist a new API key.
        Returns:
            (APIKey_model, plaintext_raw_key)
        """
        # Verify tenant exists
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        raw_key, prefix, key_hash = generate_api_key()
        key_id = f"key_{uuid.uuid4().hex[:12]}"

        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO api_keys (id, tenant_id, name, prefix, key_hash, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (key_id, tenant_id, name, prefix, key_hash),
        )
        conn.commit()

        api_key_obj = APIKey(
            id=key_id,
            tenant_id=tenant_id,
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            is_active=True,
        )
        return api_key_obj, raw_key

    def get_api_key_by_hash(self, key_hash: str) -> Optional[APIKey]:
        """Look up API key by SHA-256 hash."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, name, prefix, key_hash, is_active, created_at, last_used_at
            FROM api_keys
            WHERE key_hash = ? AND is_active = 1
            """,
            (key_hash,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return APIKey(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            prefix=row["prefix"],
            key_hash=row["key_hash"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

    def list_api_keys(self, tenant_id: str) -> list[APIKey]:
        """List all API keys for a tenant."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, name, prefix, key_hash, is_active, created_at, last_used_at
            FROM api_keys
            WHERE tenant_id = ? AND is_active = 1
            ORDER BY created_at DESC
            """,
            (tenant_id,),
        )
        rows = cur.fetchall()
        return [
            APIKey(
                id=r["id"],
                tenant_id=r["tenant_id"],
                name=r["name"],
                prefix=r["prefix"],
                key_hash=r["key_hash"],
                is_active=bool(r["is_active"]),
                created_at=r["created_at"],
                last_used_at=r["last_used_at"],
            )
            for r in rows
        ]

    def revoke_api_key(self, key_id: str, tenant_id: str) -> bool:
        """Revoke / soft-delete an API key."""
        conn = self._db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND tenant_id = ?",
            (key_id, tenant_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def update_key_last_used(self, key_id: str) -> None:
        """Update last_used_at timestamp asynchronously/quietly."""
        try:
            conn = self._db.get_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE api_keys SET last_used_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) WHERE id = ?",
                (key_id,),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Failed updating key last used: %s", exc)
