"""Rebuilds item_stats: each item's recent, time-decayed impressions and feedback.

Run by the worker every ITEM_STATS_REFRESH_SECONDS. Events are bucketed by UTC day in
SQL, and each day's count is weighted by 0.5 ** (age in days / half-life), so last
week's clicks count more than last month's. The table is replaced in one transaction:
readers see either the previous stats or the new ones, never a mix.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.item_stats import ItemStats
from app.models.recommendation_impression import RecommendationImpression
from app.models.user_feedback import FeedbackType, UserFeedback

logger = logging.getLogger(__name__)

LOCK_KEY = "item_stats:refresh"

# Which item_stats counter each feedback type adds to.
FEEDBACK_COUNTERS: dict[FeedbackType, str] = {
    FeedbackType.CLICK: "clicks",
    FeedbackType.THUMBS_UP: "positives",
    FeedbackType.THUMBS_DOWN: "negatives",
    FeedbackType.IGNORE: "negatives",
    FeedbackType.PURCHASE: "conversions",
    FeedbackType.APPLY: "conversions",
}
COUNTERS = ("impressions", "clicks", "positives", "negatives", "conversions")


@dataclass
class RefreshResult:
    items: int
    impressions_purged: int


def decay_weight(age_days: int, half_life_days: float) -> float:
    return float(0.5 ** (max(age_days, 0) / half_life_days))


def smoothed_rate(successes: float, trials: float, prior: float, strength: float) -> float:
    """A rate pulled toward `prior` when there is little data: an item shown once and
    clicked once gets about the prior, not 100%. `strength` is how many trials the prior
    is worth."""
    return (successes + prior * strength) / (trials + strength)


def _day(value: object) -> date:
    # func.date() returns a date on Postgres and an ISO string on SQLite.
    return value if isinstance(value, date) else date.fromisoformat(str(value))


async def refresh_item_stats(session: AsyncSession, now: datetime | None = None) -> RefreshResult:
    now = now or datetime.now(UTC)
    today = now.date()
    since = datetime.combine(
        today - timedelta(days=settings.ITEM_STATS_WINDOW_DAYS - 1), datetime.min.time(), UTC
    )
    half_life = settings.ITEM_STATS_HALF_LIFE_DAYS
    stats: dict[tuple[uuid.UUID, str], dict[str, float]] = defaultdict(
        lambda: dict.fromkeys(COUNTERS, 0.0)
    )

    imp = RecommendationImpression
    imp_day = func.date(imp.created_at)
    rows = await session.execute(
        select(imp.tenant_id, imp.external_item_id, imp_day, func.count())
        .where(imp.created_at >= since)
        .group_by(imp.tenant_id, imp.external_item_id, imp_day)
    )
    for tenant_id, item_id, day, count in rows.tuples():
        weight = decay_weight((today - _day(day)).days, half_life)
        stats[(tenant_id, item_id)]["impressions"] += count * weight

    fb = UserFeedback
    fb_day = func.date(fb.created_at)
    rows = await session.execute(
        select(fb.tenant_id, fb.external_item_id, fb.feedback_type, fb_day, func.count())
        .where(fb.created_at >= since)
        .group_by(fb.tenant_id, fb.external_item_id, fb.feedback_type, fb_day)
    )
    for tenant_id, item_id, feedback_type, day, count in rows.tuples():
        weight = decay_weight((today - _day(day)).days, half_life)
        stats[(tenant_id, item_id)][FEEDBACK_COUNTERS[feedback_type]] += count * weight

    await session.execute(delete(ItemStats))
    if stats:
        await session.execute(
            insert(ItemStats),
            [
                {"tenant_id": t, "external_item_id": i, **counters, "refreshed_at": now}
                for (t, i), counters in stats.items()
            ],
        )

    # Never purge impressions the stats window still reads.
    retention = max(settings.IMPRESSION_RETENTION_DAYS, settings.ITEM_STATS_WINDOW_DAYS)
    purged = await session.execute(
        delete(imp).where(imp.created_at < now - timedelta(days=retention))
    )
    await session.commit()
    purged_count = purged.rowcount or 0  # type: ignore[attr-defined]
    return RefreshResult(items=len(stats), impressions_purged=purged_count)


async def refresh_if_due(
    session_factory: async_sessionmaker[AsyncSession], redis: Redis
) -> RefreshResult | None:
    """Refresh unless another worker already did in this interval. The lock expires on
    its own, so a worker that dies mid-refresh never blocks the next one for long."""
    acquired = await redis.set(LOCK_KEY, "1", nx=True, ex=settings.ITEM_STATS_REFRESH_SECONDS)
    if not acquired:
        return None
    async with session_factory() as session:
        result = await refresh_item_stats(session)
    logger.info(
        "Item stats refreshed: %d items, %d old impressions purged",
        result.items,
        result.impressions_purged,
    )
    return result
