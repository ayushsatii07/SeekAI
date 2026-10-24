"""
FastAPI authentication and rate limiting dependencies.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, Response, status

from app.auth.limiter import RateLimiter, limiter
from app.auth.models import Tenant
from app.auth.repository import AuthRepository
from app.auth.security import decode_access_token, hash_api_key
from app.config import settings

logger = logging.getLogger(__name__)

_auth_repo: Optional[AuthRepository] = None


def get_auth_repo() -> AuthRepository:
    """Dependency for AuthRepository."""
    global _auth_repo
    if _auth_repo is None:
        _auth_repo = AuthRepository()
    return _auth_repo


async def get_current_tenant(
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    repo: AuthRepository = Depends(get_auth_repo),
    rate_limiter: RateLimiter = Depends(lambda: limiter),
) -> Tenant:
    """
    Authenticate tenant via API Key or JWT Bearer token, then enforce sliding window rate limit.
    """
    tenant: Optional[Tenant] = None

    # 1. Check API Key from X-API-Key header or Authorization Bearer sk_...
    api_key_candidate: Optional[str] = None
    bearer_token_candidate: Optional[str] = None

    if x_api_key:
        api_key_candidate = x_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        token_str = authorization[7:].strip()
        if token_str.startswith("sk_"):
            api_key_candidate = token_str
        else:
            bearer_token_candidate = token_str

    if api_key_candidate:
        # Check if master admin key
        if settings.admin_api_key and api_key_candidate == settings.admin_api_key:
            tenant = Tenant(
                id="admin",
                name="System Administrator",
                tier="enterprise",
                rate_limit_per_minute=10000,
                is_active=True,
            )
        else:
            hashed = hash_api_key(api_key_candidate)
            key_record = repo.get_api_key_by_hash(hashed)
            if not key_record or not key_record.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or revoked API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            tenant = repo.get_tenant(key_record.tenant_id)
            if not tenant or not tenant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant account is suspended or inactive",
                )
            repo.update_key_last_used(key_record.id)

    elif bearer_token_candidate:
        try:
            payload = decode_access_token(bearer_token_candidate)
            tenant = repo.get_tenant(payload.tenant_id)
            if not tenant or not tenant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant account is suspended or inactive",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 2. If no credentials provided:
    if tenant is None:
        if settings.auth_enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Provide X-API-Key or Authorization Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            # Fallback to default tenant when auth_enabled is False (development mode)
            tenant = repo.get_tenant("default") or Tenant(
                id="default",
                name="Default Tenant",
                tier="pro",
                rate_limit_per_minute=settings.rate_limit_default_per_minute,
                is_active=True,
            )

    # 3. Enforce sliding window rate limit
    limit_val = tenant.rate_limit_per_minute or settings.rate_limit_default_per_minute
    rl_result = await rate_limiter.check_rate_limit(
        identifier=tenant.id,
        limit=limit_val,
        window_seconds=settings.rate_limit_window_seconds,
    )

    # Attach standard rate limit headers
    response.headers["X-RateLimit-Limit"] = str(rl_result.limit)
    response.headers["X-RateLimit-Remaining"] = str(rl_result.remaining)
    response.headers["X-RateLimit-Reset"] = str(rl_result.reset_time)

    if not rl_result.allowed:
        response.headers["Retry-After"] = str(rl_result.retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for tenant '{tenant.id}'. Quota: {rl_result.limit} req/{settings.rate_limit_window_seconds}s.",
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    return tenant
