"""
Authentication and multi-tenancy models.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Tenant(BaseModel):
    """Multi-tenant entity with tier and quota limits."""

    id: str
    name: str
    tier: str = "free"  # "free", "pro", "enterprise"
    rate_limit_per_minute: int = 60
    is_active: bool = True
    created_at: Optional[str] = None


class TenantCreateRequest(BaseModel):
    id: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=100)
    tier: str = Field(default="free", pattern=r"^(free|pro|enterprise)$")
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)


class APIKey(BaseModel):
    """API key metadata for authentication."""

    id: str
    tenant_id: str
    name: str = ""
    prefix: str
    key_hash: str
    is_active: bool = True
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None


class APIKeyCreateRequest(BaseModel):
    tenant_id: str
    name: str = Field(default="Default Key", max_length=100)


class APIKeyResponse(BaseModel):
    """Response returned upon API key generation. The full key is only shown once."""

    id: str
    api_key: str
    prefix: str
    name: str
    tenant_id: str
    created_at: str


class TokenPayload(BaseModel):
    """Decoded JWT payload."""

    sub: str
    tenant_id: str
    tier: str = "free"
    scopes: list[str] = Field(default_factory=list)
    exp: Optional[int] = None


class TokenResponse(BaseModel):
    """JWT response structure."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: str
    tier: str


class LoginRequest(BaseModel):
    """Login / Token request."""

    tenant_id: str
    secret_or_key: Optional[str] = None


class TenantStatus(BaseModel):
    """Tenant status and current rate limit info."""

    tenant: Tenant
    rate_limit_limit: int
    rate_limit_remaining: int
    rate_limit_reset: int
