"""Per-tenant usage analytics over recommendation logs, feedback and items."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.config import settings
from app.core.database import get_db
from app.middleware.auth import AuthDep
from app.models.item import EmbeddingStatus, Item
from app.models.recommendation_impression import RecommendationImpression
from app.models.recommendation_log import QueryType, RankingVariant, RecommendationLog
from app.models.tenant import Tenant
from app.models.token_usage import TokenUsage, UsageSource
from app.models.user_feedback import FeedbackType, UserFeedback
from app.schemas.tenant import ranking_config
from app.services.recommendation.experiment import two_proportion_p_value, wilson_interval

TOP_ITEMS_LIMIT = 10
FEEDBACK_GROUPS: dict[str, tuple[FeedbackType, ...]] = {
    "engagement": (FeedbackType.CLICK, FeedbackType.THUMBS_UP),
    "conversions": (FeedbackType.PURCHASE, FeedbackType.APPLY),
    "negatives": (FeedbackType.THUMBS_DOWN, FeedbackType.IGNORE),
}


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
            "embedding_status_breakdown": await self.embedding_status(),
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

    async def ranking_experiment(self, days: int = 30) -> dict[str, Any]:
        """Engagement and conversion per impression for each ranking variant, and how the
        reranked variant compares with the similarity-only control."""
        since = datetime.now(UTC) - timedelta(days=days)
        logs = RecommendationLog
        in_period = (logs.tenant_id == self._tenant.id, logs.created_at >= since)
        counts: dict[str, dict[str, int]] = {
            v: dict.fromkeys(("queries", "impressions", *FEEDBACK_GROUPS), 0)
            for v in RankingVariant
        }

        rows = await self._session.execute(
            select(logs.ranking_variant, func.count())
            .where(*in_period)
            .group_by(logs.ranking_variant)
        )
        for variant, n in rows.tuples():
            if variant not in counts:
                continue
            counts[variant]["queries"] = n

        imp = RecommendationImpression
        rows = await self._session.execute(
            select(logs.ranking_variant, func.count())
            .join(imp, imp.recommendation_log_id == logs.id)
            .where(*in_period)
            .group_by(logs.ranking_variant)
        )
        for variant, n in rows.tuples():
            if variant not in counts:
                continue
            counts[variant]["impressions"] = n

        fb = UserFeedback
        feedback_rows = await self._session.execute(
            select(logs.ranking_variant, fb.feedback_type, func.count())
            .join(fb, fb.recommendation_log_id == logs.id)
            .where(*in_period)
            .group_by(logs.ranking_variant, fb.feedback_type)
        )
        for variant, feedback_type, n in feedback_rows.tuples():
            if variant not in counts:
                continue
            for group, types in FEEDBACK_GROUPS.items():
                if feedback_type in types:
                    counts[variant][group] += n

        def rate(successes: int, trials: int) -> dict[str, float | None]:
            interval = wilson_interval(successes, trials)
            return {
                "rate": min(successes, trials) / trials if trials else None,
                "low": interval[0] if interval else None,
                "high": interval[1] if interval else None,
            }

        def compare(group: str) -> tuple[float | None, float | None]:
            c, r = counts[RankingVariant.CONTROL], counts[RankingVariant.RERANKED]
            if not c["impressions"] or not r["impressions"]:
                return None, None
            c_rate = min(c[group], c["impressions"]) / c["impressions"]
            r_rate = min(r[group], r["impressions"]) / r["impressions"]
            lift = r_rate / c_rate - 1 if c_rate else None
            p = two_proportion_p_value(r[group], r["impressions"], c[group], c["impressions"])
            return lift, p

        engagement_lift, engagement_p = compare("engagement")
        conversion_lift, conversion_p = compare("conversions")
        return {
            "since": since,
            "days": days,
            "control_share": ranking_config(self._tenant.domain_config).control_share,
            "variants": [
                {
                    "variant": variant,
                    **c,
                    "engagement_rate": rate(c["engagement"], c["impressions"]),
                    "conversion_rate": rate(c["conversions"], c["impressions"]),
                }
                for variant, c in counts.items()
            ],
            "comparison": {
                "engagement_lift": engagement_lift,
                "engagement_p_value": engagement_p,
                "conversion_lift": conversion_lift,
                "conversion_p_value": conversion_p,
            },
        }

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

    async def tokens(self, days: int = 30) -> dict[str, Any]:
        """OpenAI embedding tokens for the last `days` UTC days, by source and by day."""
        today = datetime.now(UTC).date()
        first_day = today - timedelta(days=days - 1)
        rows = (
            await self._session.execute(
                select(TokenUsage).where(
                    TokenUsage.tenant_id == self._tenant.id, TokenUsage.day >= first_day
                )
            )
        ).scalars()
        by_source = {source: 0 for source in UsageSource}
        per_day: dict[str, dict[UsageSource, int]] = {}
        api_calls = texts = cache_hits = 0
        models: set[str] = set()
        for row in rows:
            by_source[row.source] += row.tokens
            day = per_day.setdefault(row.day.isoformat(), {s: 0 for s in UsageSource})
            day[row.source] += row.tokens
            api_calls += row.api_calls
            texts += row.texts
            cache_hits += row.cache_hits
            models.add(row.model)
        total = sum(by_source.values())
        price = settings.EMBEDDING_PRICE_PER_MILLION_TOKENS
        daily = []
        for offset in range(days):
            date = (first_day + timedelta(days=offset)).isoformat()
            day = per_day.get(date, {s: 0 for s in UsageSource})
            daily.append(
                {
                    "date": date,
                    "ingest_tokens": day[UsageSource.INGEST],
                    "query_tokens": day[UsageSource.QUERY],
                }
            )
        return {
            "days": days,
            "since": datetime.combine(first_day, datetime.min.time(), tzinfo=UTC),
            "model": ", ".join(sorted(models)) or settings.EMBEDDING_MODEL,
            "total_tokens": total,
            "by_source": by_source,
            "api_calls": api_calls,
            "texts_embedded": texts,
            "cache_hits": cache_hits,
            "price_per_million_tokens": price,
            "estimated_cost_usd": round(total * price / 1_000_000, 10),
            "daily": daily,
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

    async def embedding_status(self) -> dict[EmbeddingStatus, int]:
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
