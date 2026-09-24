"""Redis connection lifecycle and FastAPI dependency.

The client is created in the app lifespan and stored on ``app.state.redis``.
"""

import asyncio
import logging

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


async def create_redis_client() -> Redis:
    """Create a Redis client and verify connectivity (raises if unreachable)."""
    client = Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        raise
    return client


def get_redis(request: Request) -> Redis:
    client: Redis | None = getattr(request.app.state, "redis", None)
    if client is None:
        raise ServiceUnavailableError("Redis client is not initialised")
    return client


async def check_redis(client: Redis) -> bool:
    """Health-check probe: True when PING succeeds within the timeout."""
    try:
        async with asyncio.timeout(settings.HEALTH_CHECK_TIMEOUT_SECONDS):
            return bool(await client.ping())
    except Exception:
        logger.exception("Redis health check failed")
        return False
