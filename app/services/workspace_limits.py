"""Per-workspace limits: total items and recommendations per month.

They come from the workspace's plan, or from a platform admin's override (see
app/services/billing/plans.py). A limit of None means no cap. The requests-per-minute
limit is applied by the rate limiter. Like the rate limiter, the monthly counter fails
open when Redis is unreachable, rather than taking recommendations down.

Visitors of the public demo also have a cap per session and one per day for all of them
together. Those fail closed: anyone on the internet can use the demo, so when the counters
cannot be checked, the demo stops answering rather than run up OpenAI costs.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.demo import is_demo_email
from app.core.exceptions import LimitExceededError
from app.core.redis_client import get_redis
from app.middleware.auth import AuthContext, AuthDep
from app.models.item import Item
from app.models.recommendation_log import RecommendationLog
from app.models.tenant import Tenant
from app.services.billing.plans import allowance

logger = logging.getLogger(__name__)

# Chunk the existing-id lookup so large uploads stay within the database's parameter limit.
_ID_LOOKUP_CHUNK = 1000
# Kept a little longer than a month, so the key outlives the month it counts.
_MONTH_KEY_TTL_SECONDS = 40 * 24 * 60 * 60
_DAY_KEY_TTL_SECONDS = 2 * 24 * 60 * 60
DEMO_SIGN_UP = "Create your own workspace to keep going."


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class WorkspaceLimits:
    def __init__(
        self, session: AsyncSession, redis: Redis, tenant: Tenant, auth: AuthContext | None = None
    ) -> None:
        self._session = session
        self._redis = redis
        self._tenant = tenant
        self._auth = auth

    async def ensure_item_capacity(self, external_ids: list[str]) -> None:
        """Reject an upload that would take the workspace past `max_items`. Re-uploading
        items that already exist replaces them, so only new external ids count."""
        limit = allowance(self._tenant).max_items
        if limit is None:
            return
        current = await self._session.scalar(
            select(func.count()).select_from(Item).where(Item.tenant_id == self._tenant.id)
        )
        unique_ids = list(dict.fromkeys(external_ids))
        existing = 0
        for start in range(0, len(unique_ids), _ID_LOOKUP_CHUNK):
            chunk = unique_ids[start : start + _ID_LOOKUP_CHUNK]
            existing += (
                await self._session.scalar(
                    select(func.count())
                    .select_from(Item)
                    .where(Item.tenant_id == self._tenant.id, Item.external_id.in_(chunk))
                )
                or 0
            )
        new = len(unique_ids) - existing
        if (current or 0) + new > limit:
            raise LimitExceededError(
                f"This workspace can hold at most {limit} items: it has {current}, and this "
                f"upload adds {new} new ones. Delete items or contact support to raise the limit."
            )

    async def consume_queries(self, count: int = 1) -> None:
        """Count `count` recommendations against `monthly_query_limit`, or raise without
        counting them."""
        if self._auth and self._auth.user and is_demo_email(self._auth.user.email):
            await self._consume_demo(count)
        limit = allowance(self._tenant).monthly_queries
        if limit is None:
            return
        start = month_start()
        key = f"quota:queries:{self._tenant.id}:{start:%Y%m}"
        try:
            if not await self._redis.exists(key):
                # First query this month, or Redis lost the counter: start from the logs.
                served = await self._session.scalar(
                    select(func.count()).where(
                        RecommendationLog.tenant_id == self._tenant.id,
                        RecommendationLog.created_at >= start,
                    )
                )
                await self._redis.set(key, served or 0, ex=_MONTH_KEY_TTL_SECONDS, nx=True)
            used = int(await self._redis.incrby(key, count))
            if used > limit:
                await self._redis.decrby(key, count)
        except Exception:
            logger.warning("Query quota unavailable; allowing request", exc_info=True)
            return
        if used > limit:
            raise LimitExceededError(
                f"Monthly limit of {limit} recommendations reached. It resets on the 1st "
                "(UTC); contact support to raise it."
            )

    async def _consume_demo(self, count: int) -> None:
        """The demo's caps: per session key and per UTC day. Fails closed."""
        assert self._auth is not None
        day = datetime.now(UTC).strftime("%Y%m%d")
        caps = [
            (
                f"quota:demo:session:{self._auth.api_key.id}",
                settings.DEMO_QUERIES_PER_SESSION,
                settings.DEMO_SESSION_MINUTES * 60,
                f"This demo session has used its {settings.DEMO_QUERIES_PER_SESSION} searches.",
            ),
            (
                f"quota:demo:day:{self._tenant.id}:{day}",
                settings.DEMO_QUERIES_PER_DAY,
                _DAY_KEY_TTL_SECONDS,
                "The live demo has answered all its searches for today; it resets at midnight UTC.",
            ),
        ]
        counted: list[str] = []
        try:
            for key, limit, ttl, message in caps:
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.incrby(key, count)
                    pipe.expire(key, ttl, nx=True)
                    used, _ = await pipe.execute()
                counted.append(key)
                if int(used) > limit:
                    for done in counted:
                        await self._redis.decrby(done, count)
                    raise LimitExceededError(f"{message} {DEMO_SIGN_UP}")
        except LimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Demo quota unavailable; refusing the demo query", exc_info=True)
            raise LimitExceededError(
                f"The live demo is unavailable for a moment. {DEMO_SIGN_UP}"
            ) from exc


def get_workspace_limits(
    auth: AuthDep,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> WorkspaceLimits:
    return WorkspaceLimits(session, redis, auth.tenant, auth)


WorkspaceLimitsDep = Annotated[WorkspaceLimits, Depends(get_workspace_limits)]
