"""Redis-backed rate limits.

- Requests: RATE_LIMIT_RPM per API key, in fixed one-minute windows.
- Ingestion: DAILY_ITEM_LIMIT items per tenant per UTC day.

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


def get_rate_limiter(redis: Annotated[Redis, Depends(get_redis)]) -> RateLimiter:
    return RateLimiter(redis)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def enforce_request_rate(auth: AuthDep, limiter: RateLimiterDep) -> None:
    await limiter.hit_request(auth.api_key.id)
