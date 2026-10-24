"""
Security helpers for JWT token handling and API key hashing.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.auth.models import TokenPayload
from app.config import settings

logger = logging.getLogger(__name__)


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256 for secure database lookup."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "sk_live_") -> tuple[str, str, str]:
    """
    Generate a cryptographically secure random API key.

    Returns:
        (raw_api_key, prefix, key_hash)
    """
    token_entropy = secrets.token_urlsafe(32)
    raw_key = f"{prefix}{token_entropy}"
    key_prefix = raw_key[:12]
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def create_access_token(
    tenant_id: str,
    tier: str = "free",
    scopes: Optional[list[str]] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token for a tenant."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.jwt_expiration_minutes)

    to_encode = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "tier": tier,
        "scopes": scopes or ["search", "ask"],
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> TokenPayload:
    """
    Validate and decode a JWT access token.

    Raises:
        ValueError if the token is expired, invalid, or forged.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid authentication token") from exc
