"""Redis-backed rate limits.

- Requests: RATE_LIMIT_RPM per API key, in fixed one-minute windows.
- Ingestion: DAILY_ITEM_LIMIT items per tenant per UTC day (UNVERIFIED_DAILY_ITEM_LIMIT
  until the account email is verified).

If Redis is unreachable the limits are skipped (fail open) rather than taking the API down.
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.redis_client import get_redis
from app.middleware.auth import AuthDep
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit_request(self, api_key_id: uuid.UUID, limit: int | None = None) -> None:
        await self.hit(f"req:{api_key_id}", limit or settings.RATE_LIMIT_RPM)

    async def hit(self, name: str, limit: int) -> None:
        """Count one event for `name` in the current one-minute window."""
        now = time.time()
        key = f"rl:{name}:{int(now // 60)}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, 120)
                count, _ = await pipe.execute()
        except Exception:
            logger.warning("Rate limiter unavailable; allowing request", exc_info=True)
            return
        if count > limit:
            raise RateLimitError(
                f"Rate limit of {limit} requests per minute exceeded",
                retry_after=60 - int(now % 60),
            )

    async def failures(self, name: str) -> int:
        """Failures recorded for `name` in its current window (0 if Redis is down)."""
        try:
            value = await self._redis.get(f"rl:fail:{name}")
        except Exception:
            logger.warning("Rate limiter unavailable; skipping failure check", exc_info=True)
            return 0
        return int(value or 0)

    async def record_failure(self, name: str, window_seconds: int) -> int:
        """Count a failure; the window starts at the first failure and is not extended."""
        key = f"rl:fail:{name}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_seconds, nx=True)
                count, _ = await pipe.execute()
        except Exception:
            logger.warning("Rate limiter unavailable; failure not counted", exc_info=True)
            return 0
        return int(count)

    async def retry_after(self, name: str) -> int:
        try:
            ttl = await self._redis.ttl(f"rl:fail:{name}")
        except Exception:
            return 60
        return max(int(ttl), 1)

    async def clear_failures(self, name: str) -> None:
        try:
            await self._redis.delete(f"rl:fail:{name}")
        except Exception:
            logger.warning("Rate limiter unavailable; failures not cleared", exc_info=True)

    async def consume_items(
        self, tenant_id: uuid.UUID, count: int, limit: int | None = None
    ) -> None:
        """Reserve `count` items of today's ingestion quota, or raise without reserving."""
        limit = limit or settings.DAILY_ITEM_LIMIT
        now = datetime.now(UTC)
        key = f"rl:items:{tenant_id}:{now:%Y%m%d}"
        try:
            used = await self._redis.incrby(key, count)
            if used == count:
                await self._redis.expire(key, 2 * 24 * 60 * 60)
            if used > limit:
                await self._redis.decrby(key, count)
        except Exception:
            logger.warning("Rate limiter unavailable; allowing ingestion", exc_info=True)
            return
        if used > limit:
            remaining = max(limit - (used - count), 0)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            raise RateLimitError(
                f"Daily ingestion limit of {limit} items exceeded "
                f"({remaining} remaining today, {count} requested)",
                retry_after=int((midnight - now).total_seconds()) + 1,
            )


def daily_item_limit(tenant: Tenant) -> int:
    """Unverified accounts get a small allowance until they confirm their email."""
    if settings.REQUIRE_EMAIL_VERIFICATION and not tenant.email_verified:
        return min(settings.UNVERIFIED_DAILY_ITEM_LIMIT, settings.DAILY_ITEM_LIMIT)
    return settings.DAILY_ITEM_LIMIT


def get_rate_limiter(redis: Annotated[Redis, Depends(get_redis)]) -> RateLimiter:
    return RateLimiter(redis)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def enforce_request_rate(auth: AuthDep, limiter: RateLimiterDep) -> None:
    await limiter.hit_request(auth.api_key.id)
