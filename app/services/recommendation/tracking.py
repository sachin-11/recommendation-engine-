"""Recording served recommendations and user feedback on them."""

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.models.recommendation_log import RecommendationLog
from app.models.tenant import Tenant
from app.models.user_feedback import FeedbackType, UserFeedback
from app.services.recommendation.query_engine import Recommendation

log = structlog.get_logger(__name__)


def build_log(
    query_id: uuid.UUID, tenant_id: uuid.UUID, recommendation: Recommendation, latency_ms: int
) -> RecommendationLog:
    results = recommendation.results
    return RecommendationLog(
        id=query_id,
        tenant_id=tenant_id,
        query_type=recommendation.query_type,
        query_input=recommendation.query_input,
        results_count=len(results),
        top_result_external_id=results[0]["external_id"] if results else None,
        latency_ms=latency_ms,
        filters_applied=recommendation.filters,
        cache_status=recommendation.cache_status,
    )


async def save_recommendation_logs(
    session_factory: async_sessionmaker[AsyncSession], logs: list[RecommendationLog]
) -> None:
    """Runs as a background task after the response is sent, so it never adds latency.
    A failure is logged, not raised: losing a log row must not break recommendations."""
    try:
        async with session_factory() as session:
            session.add_all(logs)
            await session.commit()
    except Exception:
        log.exception("recommendation_log_write_failed", count=len(logs))


async def record_feedback(
    session: AsyncSession,
    tenant: Tenant,
    query_id: uuid.UUID,
    external_item_id: str,
    feedback_type: FeedbackType,
) -> UserFeedback:
    exists = await session.scalar(
        select(RecommendationLog.id).where(
            RecommendationLog.id == query_id, RecommendationLog.tenant_id == tenant.id
        )
    )
    if exists is None:
        raise NotFoundError(f"Query '{query_id}' not found")
    feedback = UserFeedback(
        tenant_id=tenant.id,
        recommendation_log_id=query_id,
        external_item_id=external_item_id,
        feedback_type=feedback_type,
    )
    session.add(feedback)
    await session.commit()
    return feedback
