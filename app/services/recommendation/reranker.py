"""Re-ranks vector-search candidates with what people did with them (item_stats).

    ranking_score = retrieval score (similarity, plus the keyword lift in hybrid search)
                  + engagement * (engagement rate - workspace average)
                  + conversion * (conversion rate - workspace average)
                  - negative   * (negative rate   - workspace average)
                  + popularity * log(1 + impressions) / log(1 + most impressions)

Rates are per impression and smoothed toward the workspace average (see
smoothed_rate), so an item with no history scores exactly its similarity, and one
lucky click on a rarely shown item moves it little. The weights come from the
workspace's `domain_config.ranking`.
"""

import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import SQLColumnExpression, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item_stats import ItemStats
from app.schemas.tenant import RankingConfig
from app.services.recommendation.item_stats import smoothed_rate

# How many impressions the workspace average is worth when smoothing an item's rates.
PRIOR_STRENGTH = 20.0
CANDIDATE_MULTIPLIER = 4
MAX_CANDIDATES = 200

Matches = list[dict[str, Any]]


def candidate_count(top_k: int) -> int:
    return min(max(top_k * CANDIDATE_MULTIPLIER, top_k), MAX_CANDIDATES)


def retrieval_score(match: dict[str, Any]) -> float:
    """What the search ranked by: hybrid search's fused score, else the similarity."""
    return float(match["retrieval_score"] if "retrieval_score" in match else match["score"])


def external_id(match: dict[str, Any]) -> str:
    return str((match.get("metadata") or {}).get("external_id") or match["id"])


@dataclass
class WorkspaceStats:
    """Averages over every item the workspace has shown: the priors for smoothing."""

    impressions: float
    engagement: float
    conversion: float
    negative: float
    max_impressions: float

    @property
    def has_data(self) -> bool:
        return self.impressions > 0


def _capped(successes: SQLColumnExpression[float], impressions: SQLColumnExpression[float]) -> Any:
    # LEAST() is not in SQLite, and SQLite's two-argument MIN() is not in Postgres.
    return case((successes > impressions, impressions), else_=successes)


async def workspace_stats(session: AsyncSession, tenant_id: uuid.UUID) -> WorkspaceStats:
    s = ItemStats
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(s.impressions), 0.0),
                func.coalesce(func.sum(_capped(s.clicks + s.positives, s.impressions)), 0.0),
                func.coalesce(func.sum(_capped(s.conversions, s.impressions)), 0.0),
                func.coalesce(func.sum(_capped(s.negatives, s.impressions)), 0.0),
                func.coalesce(func.max(s.impressions), 0.0),
            ).where(s.tenant_id == tenant_id)
        )
    ).one()
    total, engaged, converted, negative, most = (float(v) for v in row)
    if total <= 0:
        return WorkspaceStats(0.0, 0.0, 0.0, 0.0, 0.0)
    return WorkspaceStats(total, engaged / total, converted / total, negative / total, most)


class Reranker:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rerank(
        self,
        tenant_id: uuid.UUID,
        matches: Matches,
        top_k: int,
        config: RankingConfig,
        prior: WorkspaceStats,
    ) -> Matches:
        """The best `top_k` of `matches` by ranking score, each with `ranking_score` set.
        `score` stays the cosine similarity."""
        ids = {external_id(m) for m in matches}
        rows = await self._session.scalars(
            select(ItemStats).where(
                ItemStats.tenant_id == tenant_id, ItemStats.external_item_id.in_(ids)
            )
        )
        stats = {row.external_item_id: row for row in rows}
        scored = [
            {
                **m,
                "ranking_score": retrieval_score(m)
                + self._adjustment(stats.get(external_id(m)), config, prior),
            }
            for m in matches
        ]
        scored.sort(key=lambda m: m["ranking_score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _adjustment(item: ItemStats | None, config: RankingConfig, prior: WorkspaceStats) -> float:
        if item is None or item.impressions <= 0:
            return 0.0
        shown = item.impressions

        def lift(successes: float, average: float) -> float:
            # Feedback without impressions (sent before impressions were recorded) can
            # exceed them; a rate above 1 means nothing.
            rate = smoothed_rate(min(successes, shown), shown, average, PRIOR_STRENGTH)
            return rate - average

        popularity = (
            math.log1p(shown) / math.log1p(prior.max_impressions)
            if prior.max_impressions > 0
            else 0.0
        )
        return (
            config.engagement * lift(item.clicks + item.positives, prior.engagement)
            + config.conversion * lift(item.conversions, prior.conversion)
            - config.negative * lift(item.negatives, prior.negative)
            + config.popularity * popularity
        )
