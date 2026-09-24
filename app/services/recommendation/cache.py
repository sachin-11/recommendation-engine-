"""Short-lived Redis cache for recommendation results.

Items can change at any time, so entries live only CACHE_TTL_SECONDS. Results with
raw_data are never cached. Redis errors degrade to a cache miss.
"""

import hashlib
import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 5 * 60


class RecommendationCache:
    def __init__(self, redis: Redis | None, ttl: int = CACHE_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl

    @staticmethod
    def key(
        tenant_id: uuid.UUID, query: dict[str, Any], filters: dict[str, Any], top_k: int
    ) -> str:
        """`rec:{tenant_id}:{sha256(query + filters + top_k)}`; the query includes its type."""
        payload = json.dumps(
            {"query": query, "filters": filters, "top_k": top_k},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return f"rec:{tenant_id}:{hashlib.sha256(payload.encode()).hexdigest()}"

    async def get(self, key: str) -> list[dict[str, Any]] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
        except Exception:
            logger.warning("Recommendation cache read failed", exc_info=True)
            return None
        return json.loads(raw) if raw else None

    async def set(self, key: str, results: list[dict[str, Any]]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, json.dumps(results, ensure_ascii=False), ex=self._ttl)
        except Exception:
            logger.warning("Recommendation cache write failed", exc_info=True)
