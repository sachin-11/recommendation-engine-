"""Per-tenant usage analytics over recommendation logs, feedback and items."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.auth import AuthDep
from app.models.item import EmbeddingStatus, Item
from app.models.recommendation_log import QueryType, RecommendationLog
from app.models.tenant import Tenant
from app.models.user_feedback import FeedbackType, UserFeedback

TOP_ITEMS_LIMIT = 10


class AnalyticsService:
    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant

    async def overview(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        logs = RecommendationLog

        month_stats = (
            await self._session.execute(
                select(func.count(), func.avg(logs.latency_ms)).where(
                    logs.tenant_id == self._tenant.id, logs.created_at >= month
                )
            )
        ).one()
        today_count = await self._session.scalar(
            select(func.count()).where(logs.tenant_id == self._tenant.id, logs.created_at >= today)
        )
        queried_id = logs.query_input["external_id"].as_string()
        return {
            "total_items": await self._session.scalar(
                select(func.count()).select_from(Item).where(Item.tenant_id == self._tenant.id)
            )
            or 0,
            "total_recommendations_today": today_count or 0,
            "total_recommendations_this_month": month_stats[0] or 0,
            "avg_latency_ms": round(month_stats[1]) if month_stats[1] is not None else None,
            "top_queried_items": await self._top(
                queried_id, logs.query_type == QueryType.ITEM_ID, month
            ),
            "top_recommended_items": await self._top(
                logs.top_result_external_id, logs.top_result_external_id.is_not(None), month
            ),
            "embedding_status_breakdown": await self._embedding_status(),
            "period": {"today_since": today, "month_since": month},
        }

    async def feedback_summary(self, days: int = 30) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = await self._session.execute(
            select(UserFeedback.feedback_type, func.count())
            .where(UserFeedback.tenant_id == self._tenant.id, UserFeedback.created_at >= since)
            .group_by(UserFeedback.feedback_type)
        )
        counts = {feedback_type: 0 for feedback_type in FeedbackType}
        counts.update(dict(rows.tuples().all()))
        return {"since": since, "days": days, "total": sum(counts.values()), "by_type": counts}

    async def _top(
        self, column: ColumnElement[Any], condition: ColumnElement[bool], since: datetime
    ) -> list[dict[str, Any]]:
        count = func.count().label("count")
        rows = await self._session.execute(
            select(column.label("external_id"), count)
            .where(
                RecommendationLog.tenant_id == self._tenant.id,
                RecommendationLog.created_at >= since,
                condition,
            )
            .group_by(column)
            .order_by(count.desc(), column)
            .limit(TOP_ITEMS_LIMIT)
        )
        return [{"external_id": external_id, "count": n} for external_id, n in rows.tuples()]

    async def _embedding_status(self) -> dict[EmbeddingStatus, int]:
        rows = await self._session.execute(
            select(Item.embedding_status, func.count())
            .where(Item.tenant_id == self._tenant.id)
            .group_by(Item.embedding_status)
        )
        counts = {status: 0 for status in EmbeddingStatus}
        counts.update(dict(rows.tuples().all()))
        return counts


def get_analytics_service(
    session: Annotated[AsyncSession, Depends(get_db)], auth: AuthDep
) -> AnalyticsService:
    return AnalyticsService(session, auth.tenant)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
