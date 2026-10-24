"""
Redis-backed sliding window rate limiter with in-memory fallback.

Implements the standard distributed sliding log / sliding window algorithm:
- Entries are recorded with timestamp as score in a sorted set (ZSET).
- On each request, records older than (now - window) are pruned with ZREMRANGEBYSCORE.
- The remaining count is inspected via ZCARD.
- If under the limit, the current request timestamp is added with ZADD and TTL refreshed.
- Headers conforming to standard rate limiting specs:
    X-RateLimit-Limit
    X-RateLimit-Remaining
    X-RateLimit-Reset
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RateLimitResult:
    """Result of rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_time: int  # Unix timestamp when current window resets
    retry_after: int = 0  # Seconds to wait before retry


class MemorySlidingWindowLimiter:
    """In-memory sliding window rate limiter using timestamp deques."""

    def __init__(self) -> None:
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(
        self, key: str, limit: int, window_seconds: int = 60
    ) -> RateLimitResult:
        now = time.time()
        cutoff = now - window_seconds

        async with self._lock:
            q = self._history[key]
            # Prune timestamps outside window
            while q and q[0] <= cutoff:
                q.popleft()

            current_count = len(q)
            reset_time = int(now + window_seconds)
            if q:
                # Reset time corresponds to earliest record expiring + window
                reset_time = int(q[0] + window_seconds)

            if current_count < limit:
                q.append(now)
                remaining = limit - current_count - 1
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=max(0, remaining),
                    reset_time=reset_time,
                    retry_after=0,
                )
            else:
                retry_after = max(1, int(q[0] + window_seconds - now))
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_time=reset_time,
                    retry_after=retry_after,
                )

    async def clear(self, key: Optional[str] = None) -> None:
        async with self._lock:
            if key:
                self._history.pop(key, None)
            else:
                self._history.clear()


class RedisSlidingWindowLimiter:
    """Distributed Redis sliding window rate limiter using Redis sorted sets."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None

    async def _get_client(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=1.5,
                socket_connect_timeout=1.5,
            )
        return self._redis

    async def is_available(self) -> bool:
        """Ping Redis to check connectivity."""
        try:
            client = await self._get_client()
            await client.ping()
            return True
        except Exception as exc:
            logger.debug("Redis ping failed (%s), will fallback to memory limiter", exc)
            return False

    async def check(
        self, key: str, limit: int, window_seconds: int = 60
    ) -> RateLimitResult:
        client = await self._get_client()
        redis_key = f"ratelimit:{key}"
        now = time.time()
        window_start = now - window_seconds
        req_id = f"{now}:{uuid.uuid4().hex[:8]}"

        pipe = client.pipeline()
        # Remove items older than window
        pipe.zremrangebyscore(redis_key, 0, window_start)
        # Count remaining in window
        pipe.zcard(redis_key)
        # Get oldest item score to calculate accurate reset_time
        pipe.zrange(redis_key, 0, 0, withscores=True)
        pipe.expire(redis_key, window_seconds * 2)

        results = await pipe.execute()
        current_count = results[1]
        oldest_records = results[2]

        reset_time = int(now + window_seconds)
        if oldest_records:
            reset_time = int(float(oldest_records[0][1]) + window_seconds)

        if current_count < limit:
            # Under limit: add current timestamp
            add_pipe = client.pipeline()
            add_pipe.zadd(redis_key, {req_id: now})
            add_pipe.expire(redis_key, window_seconds * 2)
            await add_pipe.execute()

            remaining = limit - current_count - 1
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=max(0, remaining),
                reset_time=reset_time,
                retry_after=0,
            )
        else:
            earliest_ts = float(oldest_records[0][1]) if oldest_records else now
            retry_after = max(1, int(earliest_ts + window_seconds - now))
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_time=reset_time,
                retry_after=retry_after,
            )

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


class RateLimiter:
    """
    Unified Sliding Window Rate Limiter.
    Tries Redis; gracefully falls back to in-memory sliding window when Redis is offline.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or settings.redis_url
        self._redis_limiter = RedisSlidingWindowLimiter(self.redis_url)
        self._memory_limiter = MemorySlidingWindowLimiter()
        self._use_redis: Optional[bool] = None

    async def check_rate_limit(
        self,
        identifier: str,
        limit: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ) -> RateLimitResult:
        """
        Check rate limit for a tenant or identifier.
        """
        if not settings.rate_limit_enabled:
            return RateLimitResult(
                allowed=True,
                limit=999999,
                remaining=999999,
                reset_time=int(time.time() + 60),
                retry_after=0,
            )

        eff_limit = limit or settings.rate_limit_default_per_minute
        eff_window = window_seconds or settings.rate_limit_window_seconds

        # Test Redis availability on first check
        if self._use_redis is None:
            self._use_redis = await self._redis_limiter.is_available()
            if self._use_redis:
                logger.info("RateLimiter: connected to Redis at %s", self.redis_url)
            else:
                logger.info("RateLimiter: Redis unavailable, using in-memory sliding window")

        if self._use_redis:
            try:
                return await self._redis_limiter.check(identifier, eff_limit, eff_window)
            except Exception as exc:
                logger.warning("Redis rate limit check error (%s), falling back to memory", exc)
                self._use_redis = False

        return await self._memory_limiter.check(identifier, eff_limit, eff_window)

    async def reset(self, identifier: Optional[str] = None) -> None:
        """Reset limits (useful in tests)."""
        await self._memory_limiter.clear(identifier)


# Global singleton instance
limiter = RateLimiter()
