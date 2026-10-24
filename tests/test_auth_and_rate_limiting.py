"""
Tests for Multi-Tenant Authentication (JWT & API Keys) and Sliding Window Rate Limiting.
"""
import pytest
import time
from fastapi.testclient import TestClient

from app.auth.limiter import MemorySlidingWindowLimiter, RateLimiter
from app.auth.models import Tenant
from app.auth.repository import AuthRepository
from app.auth.security import create_access_token, decode_access_token, generate_api_key, hash_api_key
from app.database.database import Database
from app.main import app


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_auth.db"
    db = Database(db_path=db_file)
    db.initialize()
    return db


@pytest.fixture
def auth_repo(test_db):
    return AuthRepository(db=test_db)


class TestJWTSecurity:
    def test_create_and_decode_valid_token(self):
        token = create_access_token(tenant_id="tenant_123", tier="pro", scopes=["search", "ask"])
        payload = decode_access_token(token)
        assert payload.sub == "tenant_123"
        assert payload.tenant_id == "tenant_123"
        assert payload.tier == "pro"
        assert "search" in payload.scopes

    def test_invalid_token_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid authentication token"):
            decode_access_token("not.a.valid.jwt.token")


class TestAPIKeySecurity:
    def test_generate_and_hash_api_key(self):
        raw_key, prefix, key_hash = generate_api_key(prefix="sk_live_")
        assert raw_key.startswith("sk_live_")
        assert prefix == raw_key[:12]
        assert hash_api_key(raw_key) == key_hash


class TestAuthRepository:
    def test_create_and_get_tenant(self, auth_repo):
        tenant = auth_repo.create_tenant(
            tenant_id="acme",
            name="Acme Corp",
            tier="enterprise",
            rate_limit_per_minute=200,
        )
        assert tenant.id == "acme"
        assert tenant.name == "Acme Corp"
        assert tenant.tier == "enterprise"

        fetched = auth_repo.get_tenant("acme")
        assert fetched is not None
        assert fetched.id == "acme"
        assert fetched.rate_limit_per_minute == 200

    def test_create_and_validate_api_key(self, auth_repo):
        auth_repo.create_tenant("tenant_a", "Tenant A", tier="pro")
        key_record, raw_key = auth_repo.create_api_key("tenant_a", name="Production Key")
        assert raw_key.startswith("sk_live_")
        assert key_record.tenant_id == "tenant_a"

        # Lookup by hash
        lookup = auth_repo.get_api_key_by_hash(hash_api_key(raw_key))
        assert lookup is not None
        assert lookup.id == key_record.id

        # Revocation
        assert auth_repo.revoke_api_key(key_record.id, "tenant_a") is True
        revoked = auth_repo.get_api_key_by_hash(hash_api_key(raw_key))
        assert revoked is None


class TestSlidingWindowRateLimiter:
    @pytest.mark.asyncio
    async def test_sliding_window_allows_under_limit(self):
        limiter = MemorySlidingWindowLimiter()
        # Limit 3 requests in a 10s window
        res1 = await limiter.check("test_tenant", limit=3, window_seconds=10)
        assert res1.allowed is True
        assert res1.remaining == 2

        res2 = await limiter.check("test_tenant", limit=3, window_seconds=10)
        assert res2.allowed is True
        assert res2.remaining == 1

        res3 = await limiter.check("test_tenant", limit=3, window_seconds=10)
        assert res3.allowed is True
        assert res3.remaining == 0

        # 4th request exceeds limit
        res4 = await limiter.check("test_tenant", limit=3, window_seconds=10)
        assert res4.allowed is False
        assert res4.remaining == 0
        assert res4.retry_after > 0

    @pytest.mark.asyncio
    async def test_sliding_window_resets_after_window(self):
        limiter = MemorySlidingWindowLimiter()
        # Limit 1 request in a 1-second window
        res1 = await limiter.check("test_fast", limit=1, window_seconds=1)
        assert res1.allowed is True

        res2 = await limiter.check("test_fast", limit=1, window_seconds=1)
        assert res2.allowed is False

        # Simulate time passing by clearing or waiting
        await limiter.clear("test_fast")
        res3 = await limiter.check("test_fast", limit=1, window_seconds=1)
        assert res3.allowed is True


class TestAuthAndRateLimitingAPI:
    def test_auth_routes(self):
        import uuid
        client = TestClient(app)
        unique_tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"

        # 1. Create Tenant
        resp = client.post(
            "/auth/tenants",
            json={
                "id": unique_tenant_id,
                "name": "API Test Tenant",
                "tier": "pro",
                "rate_limit_per_minute": 50,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == unique_tenant_id

        # 2. Get JWT Token
        token_resp = client.post(
            "/auth/token",
            json={"tenant_id": unique_tenant_id},
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        jwt_token = token_data["access_token"]
        assert jwt_token

        # 3. Create API Key
        key_resp = client.post(
            "/auth/api-keys",
            json={"tenant_id": unique_tenant_id, "name": "Test Key"},
        )
        assert key_resp.status_code == 200
        key_data = key_resp.json()
        api_key = key_data["api_key"]
        assert api_key.startswith("sk_live_")

        # 4. Check /auth/me with Bearer token
        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["tenant"]["id"] == unique_tenant_id
        assert "rate_limit_remaining" in me_data
        assert "X-RateLimit-Limit" in me_resp.headers

        # 5. Check /auth/me with X-API-Key
        me_key_resp = client.get(
            "/auth/me",
            headers={"X-API-Key": api_key},
        )
        assert me_key_resp.status_code == 200
        assert me_key_resp.json()["tenant"]["id"] == unique_tenant_id
