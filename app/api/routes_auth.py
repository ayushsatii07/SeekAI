"""
Authentication & Multi-Tenant management routes.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_auth_repo, get_current_tenant
from app.auth.limiter import limiter
from app.auth.models import (
    APIKeyCreateRequest,
    APIKeyResponse,
    LoginRequest,
    Tenant,
    TenantCreateRequest,
    TenantStatus,
    TokenResponse,
)
from app.auth.repository import AuthRepository
from app.auth.security import create_access_token
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    body: LoginRequest,
    repo: AuthRepository = Depends(get_auth_repo),
) -> TokenResponse:
    """Generate a JWT access token for a tenant."""
    tenant = repo.get_tenant(body.tenant_id)
    if not tenant:
        # Auto-provision if default, else raise
        if body.tenant_id == "default":
            tenant = repo.create_tenant("default", "Default Organization", "pro", 120)
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{body.tenant_id}' not found",
            )

    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is inactive",
        )

    token = create_access_token(
        tenant_id=tenant.id,
        tier=tenant.tier,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expiration_minutes * 60,
        tenant_id=tenant.id,
        tier=tenant.tier,
    )


@router.post("/tenants", response_model=Tenant)
async def create_tenant(
    body: TenantCreateRequest,
    repo: AuthRepository = Depends(get_auth_repo),
) -> Tenant:
    """Register a new multi-tenant account."""
    existing = repo.get_tenant(body.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant '{body.id}' already exists",
        )
    return repo.create_tenant(
        tenant_id=body.id,
        name=body.name,
        tier=body.tier,
        rate_limit_per_minute=body.rate_limit_per_minute,
    )


@router.get("/tenants", response_model=list[Tenant])
async def list_tenants(
    repo: AuthRepository = Depends(get_auth_repo),
) -> list[Tenant]:
    """List registered tenants."""
    return repo.list_tenants()


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    body: APIKeyCreateRequest,
    repo: AuthRepository = Depends(get_auth_repo),
) -> APIKeyResponse:
    """Create a new API key for a tenant. Note: the secret API key is returned ONLY once."""
    try:
        key_record, raw_key = repo.create_api_key(body.tenant_id, body.name)
        return APIKeyResponse(
            id=key_record.id,
            api_key=raw_key,
            prefix=key_record.prefix,
            name=key_record.name,
            tenant_id=key_record.tenant_id,
            created_at=key_record.created_at or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api-keys")
async def list_api_keys(
    tenant_id: Optional[str] = Query(None),
    current_tenant: Tenant = Depends(get_current_tenant),
    repo: AuthRepository = Depends(get_auth_repo),
):
    """List active API keys for tenant."""
    target_tenant_id = tenant_id or current_tenant.id
    keys = repo.list_api_keys(target_tenant_id)
    return [
        {
            "id": k.id,
            "tenant_id": k.tenant_id,
            "name": k.name,
            "prefix": k.prefix,
            "is_active": k.is_active,
            "created_at": k.created_at,
            "last_used_at": k.last_used_at,
        }
        for k in keys
    ]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    current_tenant: Tenant = Depends(get_current_tenant),
    repo: AuthRepository = Depends(get_auth_repo),
):
    """Revoke an API key."""
    success = repo.revoke_api_key(key_id, current_tenant.id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found or not owned by tenant")
    return {"message": f"API key {key_id} successfully revoked"}


@router.get("/me", response_model=TenantStatus)
async def get_current_tenant_status(
    current_tenant: Tenant = Depends(get_current_tenant),
) -> TenantStatus:
    """Check current authenticated tenant and live rate limit quota."""
    rl = await limiter.check_rate_limit(
        identifier=current_tenant.id,
        limit=current_tenant.rate_limit_per_minute,
        window_seconds=settings.rate_limit_window_seconds,
    )
    return TenantStatus(
        tenant=current_tenant,
        rate_limit_limit=rl.limit,
        rate_limit_remaining=rl.remaining,
        rate_limit_reset=rl.reset_time,
    )
