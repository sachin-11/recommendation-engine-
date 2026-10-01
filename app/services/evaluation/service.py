"""Golden-set management and offline evaluation runs.

A run asks the query engine for every golden query once per variant (a set of ranking
settings), and scores each answer with NDCG, recall and MRR. Settings are passed to the
engine for the call only; nothing is saved. Runs serve no user: they ask as no one (no
user_id, so no personalization), never sit in an A/B control group, and are not logged
or counted against the monthly query limit. Query embeddings are cached, so a repeat run
costs little.
"""

import uuid
from statistics import fmean
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.middleware.auth import AuthDep
from app.models.eval_query import EvalQuery
from app.models.tenant import Tenant
from app.schemas.evaluation import MAX_EVAL_QUERIES, EvalQueryIn, EvalVariantIn, RankingOverride
from app.schemas.tenant import RankingConfig, ranking_config
from app.services.evaluation.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from app.services.recommendation.query_engine import QueryEngine

# Used for the default "hybrid" variant when the workspace has keyword matching off.
DEFAULT_KEYWORD = 0.3


def default_variants(saved: RankingConfig) -> list[EvalVariantIn]:
    """Vector search alone, hybrid search without feedback, and the saved settings."""
    no_feedback = {"engagement": 0, "conversion": 0, "negative": 0, "popularity": 0}
    return [
        EvalVariantIn(name="vector", ranking=RankingOverride(enabled=False)),
        EvalVariantIn(
            name="hybrid",
            ranking=RankingOverride(
                enabled=True, keyword=saved.keyword or DEFAULT_KEYWORD, **no_feedback
            ),
        ),
        EvalVariantIn(name="current", ranking=RankingOverride()),
    ]


def resolve(saved: RankingConfig, override: RankingOverride) -> RankingConfig:
    """The saved settings with the override applied, and no A/B control group."""
    changes = override.model_dump(exclude_none=True)
    return RankingConfig.model_validate({**saved.model_dump(), **changes, "control_share": 0})


class EvaluationService:
    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant

    # --- Golden set ---

    async def list_queries(self) -> list[EvalQuery]:
        rows = await self._session.scalars(
            select(EvalQuery)
            .where(EvalQuery.tenant_id == self._tenant.id)
            .order_by(EvalQuery.created_at, EvalQuery.query)
        )
        return list(rows)

    async def save_query(self, data: EvalQueryIn) -> tuple[EvalQuery, bool]:
        """Add a query, or replace the relevant items of the same query. True if new."""
        existing = await self._session.scalar(
            select(EvalQuery).where(
                EvalQuery.tenant_id == self._tenant.id, EvalQuery.query == data.query
            )
        )
        if existing is not None:
            existing.relevant = data.relevant
            await self._session.commit()
            return existing, False
        count = await self._session.scalar(
            select(func.count()).where(EvalQuery.tenant_id == self._tenant.id)
        )
        if (count or 0) >= MAX_EVAL_QUERIES:
            raise BadRequestError(f"A golden set holds at most {MAX_EVAL_QUERIES} queries")
        query = EvalQuery(tenant_id=self._tenant.id, query=data.query, relevant=data.relevant)
        self._session.add(query)
        await self._session.commit()
        return query, True

    async def delete_query(self, query_id: uuid.UUID) -> None:
        query = await self._session.scalar(
            select(EvalQuery).where(
                EvalQuery.id == query_id, EvalQuery.tenant_id == self._tenant.id
            )
        )
        if query is None:
            raise NotFoundError(f"Evaluation query '{query_id}' not found")
        await self._session.delete(query)
        await self._session.commit()

    # --- Runs ---

    async def run(
        self, engine: QueryEngine, k: int, variants: list[EvalVariantIn] | None
    ) -> dict[str, Any]:
        queries = await self.list_queries()
        if not queries:
            raise BadRequestError("Add golden queries first: POST /evaluation/queries")
        saved = ranking_config(self._tenant.domain_config)
        chosen = variants or default_variants(saved)
        names = [v.name for v in chosen]
        if len(set(names)) != len(names):
            raise BadRequestError("Variant names must be unique")

        per_query: list[dict[str, Any]] = [
            {"id": q.id, "query": q.query, "relevant": q.relevant, "by_variant": {}, "top": {}}
            for q in queries
        ]
        results = []
        for variant in chosen:
            config = resolve(saved, variant.ranking)
            scores = []
            for query, row in zip(queries, per_query, strict=True):
                answer = await engine.recommend_by_text(
                    query.query, self._tenant, top_k=k, ranking=config
                )
                ranked = [r["external_id"] for r in answer.results]
                metrics = {
                    "ndcg": ndcg_at_k(ranked, query.relevant, k),
                    "recall": recall_at_k(ranked, query.relevant, k),
                    "mrr": reciprocal_rank(ranked, query.relevant, k),
                }
                row["by_variant"][variant.name] = metrics
                row["top"][variant.name] = ranked
                scores.append(metrics)
            results.append(
                {
                    "name": variant.name,
                    "ranking": config,
                    **{m: fmean(s[m] for s in scores) for m in ("ndcg", "recall", "mrr")},
                }
            )
        return {"k": k, "queries": len(queries), "variants": results, "per_query": per_query}


def get_evaluation_service(
    session: Annotated[AsyncSession, Depends(get_db)], auth: AuthDep
) -> EvaluationService:
    return EvaluationService(session, auth.tenant)


EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
