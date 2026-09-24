"""Recommendation queries and feedback. All routes require an X-API-Key header."""

import time
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.database import get_db, get_session_factory
from app.core.metrics import RECO_LATENCY, RECO_REQUESTS
from app.middleware.auth import AuthDep
from app.schemas.common import ErrorResponse
from app.schemas.recommend import (
    BatchRecommendRequest,
    BatchRecommendResponse,
    FeedbackRequest,
    FeedbackResponse,
    ItemRecommendRequest,
    ProfileRecommendRequest,
    RecommendResponse,
    TextRecommendRequest,
)
from app.services.recommendation.dependencies import QueryEngineDep
from app.services.recommendation.query_engine import BatchQuery, Recommendation
from app.services.recommendation.tracking import (
    build_log,
    record_feedback,
    save_recommendation_logs,
)

log = structlog.get_logger(__name__)

CACHE_HEADER = "X-Cache"
_QUERY_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Invalid filters or query"},
    503: {
        "model": ErrorResponse,
        "description": "OpenAI or Pinecone unavailable or timed out (see Retry-After)",
    },
}

router = APIRouter(
    prefix="/recommend", tags=["recommend"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


class Responder:
    """Finishes a recommendation request: measures latency, sets X-Cache, logs, and
    queues the RecommendationLog write to run after the response is sent."""

    def __init__(
        self,
        request: Request,
        response: Response,
        auth: AuthDep,
        background_tasks: BackgroundTasks,
        session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    ) -> None:
        self._request = request
        self._response = response
        self._tenant_id = auth.tenant.id
        self._background_tasks = background_tasks
        self._session_factory = session_factory

    def single(self, recommendation: Recommendation) -> RecommendResponse:
        latency_ms = self._latency_ms()
        query_id = uuid.uuid4()
        self._save_logs([(query_id, recommendation)], latency_ms)
        self._response.headers[CACHE_HEADER] = recommendation.cache_status
        return RecommendResponse(
            results=recommendation.results,
            total=len(recommendation.results),
            query_id=query_id,
            latency_ms=latency_ms,
            embedding_tokens=recommendation.embedding_tokens,
            request_id=self._request_id,
        )

    def batch(self, recommendations: dict[str, Recommendation]) -> BatchRecommendResponse:
        latency_ms = self._latency_ms()
        query_ids = {qid: uuid.uuid4() for qid in recommendations}
        self._save_logs([(query_ids[q], r) for q, r in recommendations.items()], latency_ms)
        statuses = {r.cache_status for r in recommendations.values()}
        self._response.headers[CACHE_HEADER] = statuses.pop() if len(statuses) == 1 else "PARTIAL"
        return BatchRecommendResponse(
            results={qid: r.results for qid, r in recommendations.items()},
            query_ids=query_ids,
            latency_ms=latency_ms,
            embedding_tokens=sum(r.embedding_tokens for r in recommendations.values()),
            request_id=self._request_id,
        )

    def _save_logs(self, entries: list[tuple[uuid.UUID, Recommendation]], latency_ms: int) -> None:
        logs = [build_log(qid, self._tenant_id, rec, latency_ms) for qid, rec in entries]
        self._background_tasks.add_task(save_recommendation_logs, self._session_factory, logs)
        for (_, rec), entry in zip(entries, logs, strict=True):
            RECO_REQUESTS.labels(str(self._tenant_id), rec.query_type.value).inc()
            RECO_LATENCY.labels(rec.query_type.value).observe(latency_ms / 1000)
            log.info(
                "recommendation_served",
                tenant_id=str(self._tenant_id),
                query_id=str(entry.id),
                query_type=rec.query_type.value,
                latency_ms=latency_ms,
                result_count=len(rec.results),
                cache=rec.cache_status,
                embedding_tokens=rec.embedding_tokens,
            )

    def _latency_ms(self) -> int:
        started = getattr(self._request.state, "started_at", None) or time.perf_counter()
        return round((time.perf_counter() - started) * 1000)

    @property
    def _request_id(self) -> str:
        return getattr(self._request.state, "request_id", "")


ResponderDep = Annotated[Responder, Depends()]


@router.post(
    "/by-text",
    summary="Recommend items matching free text",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def recommend_by_text(
    payload: TextRecommendRequest, auth: AuthDep, engine: QueryEngineDep, respond: ResponderDep
) -> RecommendResponse:
    recommendation = await engine.recommend_by_text(
        payload.query, auth.tenant, payload.top_k, payload.filters, payload.include_raw_data
    )
    return respond.single(recommendation)


@router.post(
    "/by-item",
    summary="Recommend items similar to an existing item",
    responses={
        **_QUERY_RESPONSES,
        409: {"model": ErrorResponse, "description": "Item is not embedded yet"},
    },
    response_model_exclude_none=True,
)
async def recommend_by_item(
    payload: ItemRecommendRequest, auth: AuthDep, engine: QueryEngineDep, respond: ResponderDep
) -> RecommendResponse:
    recommendation = await engine.recommend_by_item_id(
        payload.external_id, auth.tenant, payload.top_k, payload.filters, payload.include_raw_data
    )
    return respond.single(recommendation)


@router.post(
    "/by-profile",
    summary="Recommend items matching a profile (e.g. a candidate's resume fields)",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def recommend_by_profile(
    payload: ProfileRecommendRequest, auth: AuthDep, engine: QueryEngineDep, respond: ResponderDep
) -> RecommendResponse:
    recommendation = await engine.recommend_by_profile(
        payload.profile, auth.tenant, payload.top_k, payload.filters, payload.include_raw_data
    )
    return respond.single(recommendation)


@router.post(
    "/batch",
    summary="Run up to 20 text queries in one request",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def recommend_batch(
    payload: BatchRecommendRequest, auth: AuthDep, engine: QueryEngineDep, respond: ResponderDep
) -> BatchRecommendResponse:
    queries = [BatchQuery(q.id, q.query, q.filters) for q in payload.queries]
    recommendations = await engine.recommend_batch(queries, auth.tenant, payload.top_k)
    return respond.batch(recommendations)


@router.post(
    "/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Record how a user reacted to a recommended item",
)
async def submit_feedback(
    payload: FeedbackRequest,
    auth: AuthDep,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackResponse:
    feedback = await record_feedback(
        session, auth.tenant, payload.query_id, payload.external_item_id, payload.feedback_type
    )
    return FeedbackResponse.model_validate(feedback)
