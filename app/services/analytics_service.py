"""Per-tenant usage analytics over recommendation logs, feedback and items."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

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

    async def usage(self, days: int = 30) -> dict[str, Any]:
        """Daily volume and latency (zero-filled, oldest first), query types, cache hit rate
        and feedback count for the last `days` UTC days including today."""
        today = datetime.now(UTC).date()
        first_day = today - timedelta(days=days - 1)
        since = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
        logs = RecommendationLog
        in_period = (logs.tenant_id == self._tenant.id, logs.created_at >= since)

        day = func.date(logs.created_at)
        rows = await self._session.execute(
            select(day, func.count(), func.avg(logs.latency_ms)).where(*in_period).group_by(day)
        )
        per_day = {str(d): (n, avg) for d, n, avg in rows.tuples()}
        daily = []
        for offset in range(days):
            date = (first_day + timedelta(days=offset)).isoformat()
            count, avg = per_day.get(date, (0, None))
            daily.append(
                {
                    "date": date,
                    "count": count,
                    "avg_latency_ms": round(avg) if avg is not None else None,
                }
            )

        type_rows = await self._session.execute(
            select(logs.query_type, func.count()).where(*in_period).group_by(logs.query_type)
        )
        by_type = {query_type: 0 for query_type in QueryType}
        by_type.update(dict(type_rows.tuples().all()))

        cache_rows = await self._session.execute(
            select(logs.cache_status, func.count())
            .where(*in_period, logs.cache_status.in_(("HIT", "MISS", "PARTIAL")))
            .group_by(logs.cache_status)
        )
        cache = dict(cache_rows.tuples().all())
        cacheable = sum(cache.values())

        feedback = await self._session.scalar(
            select(func.count()).where(
                UserFeedback.tenant_id == self._tenant.id, UserFeedback.created_at >= since
            )
        )
        return {
            "days": days,
            "since": since,
            "total_recommendations": sum(d["count"] for d in daily),
            "daily": daily,
            "by_query_type": by_type,
            "cache_hit_rate": round(cache.get("HIT", 0) / cacheable, 4) if cacheable else None,
            "feedback_total": feedback or 0,
        }

    async def _top(
        self,
        column: ColumnElement[Any] | InstrumentedAttribute[Any],
        condition: ColumnElement[bool],
        since: datetime,
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
