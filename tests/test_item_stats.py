import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    DomainType,
    FeedbackType,
    ItemStats,
    QueryType,
    RecommendationImpression,
    RecommendationLog,
    Tenant,
    UserFeedback,
)
from app.services.recommendation.item_stats import (
    decay_weight,
    refresh_if_due,
    refresh_item_stats,
    smoothed_rate,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


class Events:
    """Writes impressions and feedback for one tenant at chosen ages."""

    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant
        self._rank = 0
        self._log = RecommendationLog(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            query_type=QueryType.TEXT,
            query_input={"query": "x"},
            results_count=0,
            latency_ms=1,
            filters_applied={},
        )
        session.add(self._log)

    @classmethod
    async def create(cls, session: AsyncSession, tenant: Tenant) -> "Events":
        events = cls(session, tenant)
        await session.flush()  # the log first: impressions reference it
        return events

    def shown(self, item: str, days_ago: float, times: int = 1) -> None:
        for _ in range(times):
            self._rank += 1
            self._session.add(
                RecommendationImpression(
                    recommendation_log_id=self._log.id,
                    rank=self._rank,
                    tenant_id=self._tenant.id,
                    external_item_id=item,
                    score=0.5,
                    created_at=NOW - timedelta(days=days_ago),
                )
            )

    def feedback(self, item: str, feedback_type: FeedbackType, days_ago: float) -> None:
        self._session.add(
            UserFeedback(
                tenant_id=self._tenant.id,
                recommendation_log_id=self._log.id,
                external_item_id=item,
                feedback_type=feedback_type,
                created_at=NOW - timedelta(days=days_ago),
            )
        )


async def _tenant(session: AsyncSession, email: str) -> Tenant:
    tenant = Tenant(name=email, email=email, domain_type=DomainType.CUSTOM, domain_config={})
    session.add(tenant)
    await session.flush()
    return tenant


async def _stats(session: AsyncSession) -> dict[tuple[uuid.UUID, str], ItemStats]:
    rows = await session.scalars(select(ItemStats))
    return {(r.tenant_id, r.external_item_id): r for r in rows}


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def test_counts_are_decayed_by_age_and_mapped_by_type(session: AsyncSession) -> None:
    tenant = await _tenant(session, "a@example.com")
    events = await Events.create(session, tenant)
    events.shown("job-1", days_ago=0, times=2)
    events.shown("job-1", days_ago=7)
    events.shown("job-2", days_ago=14)
    events.feedback("job-1", FeedbackType.CLICK, days_ago=0)
    events.feedback("job-1", FeedbackType.THUMBS_UP, days_ago=0)
    events.feedback("job-1", FeedbackType.APPLY, days_ago=7)
    events.feedback("job-1", FeedbackType.THUMBS_DOWN, days_ago=0)
    events.feedback("job-1", FeedbackType.IGNORE, days_ago=0)
    await session.commit()

    result = await refresh_item_stats(session, now=NOW)

    stats = await _stats(session)
    assert result.items == 2
    job1 = stats[(tenant.id, "job-1")]
    assert job1.impressions == pytest.approx(2.5)  # 2 today + 1 a half-life ago
    assert (job1.clicks, job1.positives, job1.negatives) == (1.0, 1.0, 2.0)
    assert job1.conversions == pytest.approx(0.5)
    assert stats[(tenant.id, "job-2")].impressions == pytest.approx(0.25)
    assert stats[(tenant.id, "job-2")].clicks == 0.0


async def test_window_tenants_and_replacement(session: AsyncSession) -> None:
    first = await _tenant(session, "a@example.com")
    second = await _tenant(session, "b@example.com")
    (await Events.create(session, first)).shown("job-1", days_ago=1)
    (await Events.create(session, first)).shown("stale", days_ago=40)  # outside the 30-day window
    (await Events.create(session, second)).shown("job-1", days_ago=1)
    session.add(ItemStats(tenant_id=first.id, external_item_id="gone", impressions=9.0))
    await session.commit()

    await refresh_item_stats(session, now=NOW)

    assert set(await _stats(session)) == {(first.id, "job-1"), (second.id, "job-1")}


async def test_old_impressions_are_purged(session: AsyncSession) -> None:
    tenant = await _tenant(session, "a@example.com")
    events = await Events.create(session, tenant)
    events.shown("job-1", days_ago=89)
    events.shown("job-1", days_ago=91)
    await session.commit()

    result = await refresh_item_stats(session, now=NOW)

    assert result.impressions_purged == 1
    kept = (await session.scalars(select(RecommendationImpression))).all()
    assert len(kept) == 1


async def test_refresh_runs_once_per_interval(
    session_factory: async_sessionmaker[AsyncSession], redis: fakeredis.FakeAsyncRedis
) -> None:
    assert await refresh_if_due(session_factory, redis) is not None
    assert await refresh_if_due(session_factory, redis) is None  # another worker's turn

    await redis.flushall()
    assert await refresh_if_due(session_factory, redis) is not None


def test_decay_and_smoothing() -> None:
    assert decay_weight(0, 7) == 1.0
    assert decay_weight(14, 7) == pytest.approx(0.25)
    assert smoothed_rate(0, 0, prior=0.1, strength=20) == pytest.approx(0.1)
    # One impression and one click is barely evidence; it stays near the prior.
    assert smoothed_rate(1, 1, prior=0.1, strength=20) == pytest.approx(3 / 21)
    assert smoothed_rate(500, 1000, prior=0.1, strength=20) == pytest.approx(502 / 1020)
