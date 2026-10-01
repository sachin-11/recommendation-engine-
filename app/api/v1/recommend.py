"""Recommendation queries and feedback. All routes require an X-API-Key header."""

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.database import get_db, get_session_factory
from app.core.metrics import RECO_LATENCY, RECO_REQUESTS
from app.core.redis_client import get_redis
from app.middleware.auth import AuthDep
from app.models.item import Item
from app.models.recommendation_log import QueryType
from app.schemas.common import ErrorResponse
from app.schemas.recommend import (
    AskRequest,
    AskResponse,
    BatchRecommendRequest,
    BatchRecommendResponse,
    FeedbackRequest,
    FeedbackResponse,
    InterpretationOut,
    ItemRecommendRequest,
    ProfileRecommendRequest,
    RecommendResponse,
    TextRecommendRequest,
)
from app.services.embedding.text_builder import TextBuilder
from app.services.recommendation.dependencies import (
    QueryEngineDep,
    QueryUnderstandingDep,
    SummarizerDep,
)
from app.services.recommendation.query_engine import BatchQuery, Recommendation
from app.services.recommendation.query_understanding import cached_profiles
from app.services.recommendation.summarizer import SUMMARY_ITEMS, SummaryUsage
from app.services.recommendation.tracking import (
    build_impressions,
    build_log,
    record_feedback,
    save_recommendation_logs,
)
from app.services.workspace_limits import WorkspaceLimitsDep

log = structlog.get_logger(__name__)

CACHE_HEADER = "X-Cache"
_QUERY_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Invalid filters or query"},
    403: {"model": ErrorResponse, "description": "Monthly recommendation limit reached"},
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

    def single(self, recommendation: Recommendation, user_id: str | None) -> RecommendResponse:
        latency_ms = self._latency_ms()
        query_id = uuid.uuid4()
        self._save_logs([(query_id, recommendation)], latency_ms, user_id)
        self._response.headers[CACHE_HEADER] = recommendation.cache_status
        return RecommendResponse(
            results=recommendation.results,
            total=len(recommendation.results),
            query_id=query_id,
            latency_ms=latency_ms,
            embedding_tokens=recommendation.embedding_tokens,
            rerank_tokens=recommendation.rerank_tokens,
            request_id=self._request_id,
        )

    def batch(
        self, recommendations: dict[str, Recommendation], user_id: str | None
    ) -> BatchRecommendResponse:
        latency_ms = self._latency_ms()
        query_ids = {qid: uuid.uuid4() for qid in recommendations}
        self._save_logs(
            [(query_ids[q], r) for q, r in recommendations.items()], latency_ms, user_id
        )
        statuses = {r.cache_status for r in recommendations.values()}
        self._response.headers[CACHE_HEADER] = statuses.pop() if len(statuses) == 1 else "PARTIAL"
        return BatchRecommendResponse(
            results={qid: r.results for qid, r in recommendations.items()},
            query_ids=query_ids,
            latency_ms=latency_ms,
            embedding_tokens=sum(r.embedding_tokens for r in recommendations.values()),
            rerank_tokens=sum(r.rerank_tokens for r in recommendations.values()),
            request_id=self._request_id,
        )

    def _save_logs(
        self,
        entries: list[tuple[uuid.UUID, Recommendation]],
        latency_ms: int,
        user_id: str | None,
    ) -> None:
        logs = [build_log(qid, self._tenant_id, rec, latency_ms, user_id) for qid, rec in entries]
        impressions = [
            row for qid, rec in entries for row in build_impressions(qid, self._tenant_id, rec)
        ]
        self._background_tasks.add_task(
            save_recommendation_logs, self._session_factory, logs, impressions
        )
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
                rerank_tokens=rec.rerank_tokens,
                rerank_cost_usd=rec.rerank_cost_usd,
                rerank_fallback=rec.rerank_fallback,
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
    payload: TextRecommendRequest,
    auth: AuthDep,
    engine: QueryEngineDep,
    respond: ResponderDep,
    limits: WorkspaceLimitsDep,
) -> RecommendResponse:
    await limits.consume_queries()
    recommendation = await engine.recommend_by_text(
        payload.query,
        auth.tenant,
        payload.top_k,
        payload.filters,
        payload.include_raw_data,
        payload.user_id,
    )
    return respond.single(recommendation, payload.user_id)


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
    payload: ItemRecommendRequest,
    auth: AuthDep,
    engine: QueryEngineDep,
    respond: ResponderDep,
    limits: WorkspaceLimitsDep,
) -> RecommendResponse:
    await limits.consume_queries()
    recommendation = await engine.recommend_by_item_id(
        payload.external_id,
        auth.tenant,
        payload.top_k,
        payload.filters,
        payload.include_raw_data,
        payload.user_id,
    )
    return respond.single(recommendation, payload.user_id)


@router.post(
    "/by-profile",
    summary="Recommend items matching a profile (e.g. a candidate's resume fields)",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def recommend_by_profile(
    payload: ProfileRecommendRequest,
    auth: AuthDep,
    engine: QueryEngineDep,
    respond: ResponderDep,
    limits: WorkspaceLimitsDep,
) -> RecommendResponse:
    await limits.consume_queries()
    recommendation = await engine.recommend_by_profile(
        payload.profile,
        auth.tenant,
        payload.top_k,
        payload.filters,
        payload.include_raw_data,
        payload.user_id,
    )
    return respond.single(recommendation, payload.user_id)


class Asker:
    """Answers a question in plain language: read it into search text and filters, search,
    and relax the filters if they match nothing. Shared by /ask and /ask/stream."""

    def __init__(
        self,
        auth: AuthDep,
        engine: QueryEngineDep,
        understanding: QueryUnderstandingDep,
        respond: ResponderDep,
        limits: WorkspaceLimitsDep,
        session: Annotated[AsyncSession, Depends(get_db)],
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> None:
        self._tenant = auth.tenant
        self._engine = engine
        self._understanding = understanding
        self._respond = respond
        self._limits = limits
        self._session = session
        self._redis = redis

    async def answer(self, payload: AskRequest) -> AskResponse:
        await self._limits.consume_queries()
        tenant = self._tenant
        profiles = await cached_profiles(self._redis, self._session, tenant)
        meaning = await self._understanding.interpret(
            payload.question, profiles, tenant.domain_config
        )

        async def search(filters: dict[str, Any]) -> Recommendation:
            return await self._engine.recommend_by_text(
                meaning.search_text,
                tenant,
                payload.top_k,
                filters,
                payload.include_raw_data,
                payload.user_id,
            )

        recommendation = await search(meaning.filters)
        # Filters that match nothing help no one: show the closest items without them.
        relaxed = bool(meaning.filters) and not recommendation.results
        if relaxed:
            recommendation = await search({})
        recommendation.query_type = QueryType.ASK
        recommendation.query_input = {"question": payload.question, "query": meaning.search_text}
        log.info(
            "question_understood",
            tenant_id=str(tenant.id),
            filters=meaning.filters,
            ignored=meaning.ignored,
            fallback=meaning.fallback,
            cached=meaning.cached,
            understand_tokens=meaning.prompt_tokens + meaning.completion_tokens,
            understand_cost_usd=meaning.cost_usd,
            relaxed=relaxed,
        )
        response = self._respond.single(recommendation, payload.user_id)
        return AskResponse(
            **response.model_dump(),
            interpretation=InterpretationOut(
                search_text=meaning.search_text,
                filters=meaning.filters,
                ignored=meaning.ignored,
                fallback=meaning.fallback,
            ),
            relaxed=relaxed,
            understand_tokens=meaning.prompt_tokens + meaning.completion_tokens,
        )

    async def item_texts(self, external_ids: list[str]) -> list[tuple[str, str]]:
        """(external id, text) for the given items, in order, as the summarizer reads them."""
        if not external_ids:
            return []
        rows = await self._session.execute(
            select(Item.external_id, Item.raw_data).where(
                Item.tenant_id == self._tenant.id, Item.external_id.in_(external_ids)
            )
        )
        raw = dict(rows.tuples().all())
        builder = TextBuilder()
        return [
            (i, builder.build_embedding_text(raw[i], self._tenant.domain_config))
            for i in external_ids
            if i in raw
        ]


AskerDep = Annotated[Asker, Depends()]


@router.post(
    "/ask",
    summary="Recommend items for a question in plain language",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def ask(payload: AskRequest, asker: AskerDep) -> AskResponse:
    return await asker.answer(payload)


def _event(name: str, data: Any) -> str:
    """One Server-Sent Event."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post(
    "/ask/stream",
    summary="Ask in plain language; results and a written answer stream as Server-Sent Events",
    responses={
        **_QUERY_RESPONSES,
        200: {
            "description": "A text/event-stream: interpretation, results, summary (repeated), "
            "done; or error after the results if the summary fails.",
            "content": {"text/event-stream": {}},
        },
    },
)
async def ask_stream(
    payload: AskRequest, asker: AskerDep, summarizer: SummarizerDep
) -> StreamingResponse:
    # Understanding and search finish first, so their errors are normal HTTP errors and
    # the database session is not held open while the summary streams.
    answer = await asker.answer(payload)
    top = answer.results[:SUMMARY_ITEMS]
    texts = await asker.item_texts([r.external_id for r in top]) if summarizer.available else []
    # The filter fields too (e.g. location), so the answer can speak to "not in Pune".
    metadata = {r.external_id: r.metadata for r in top}
    items = [
        (i, " ".join([text, *(f"{k}: {v}" for k, v in metadata.get(i, {}).items())]))
        for i, text in texts
    ]

    async def events() -> AsyncIterator[str]:
        body = answer.model_dump(mode="json", exclude_none=True)
        yield _event("interpretation", body.pop("interpretation"))
        yield _event("results", body)
        usage = SummaryUsage()
        if not items:
            yield _event("done", {"summary_tokens": 0, "summary_cost_usd": 0.0})
            return
        try:
            async for piece in summarizer.stream(payload.question, items, usage):
                yield _event("summary", {"text": piece})
        except Exception as exc:
            log.warning("summary_failed", error=repr(exc))
            yield _event("error", {"message": "The summary could not be written."})
            return
        yield _event(
            "done",
            {
                "summary_tokens": usage.prompt_tokens + usage.completion_tokens,
                "summary_cost_usd": usage.cost_usd,
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # No proxy buffering, so each event reaches the browser as it is written.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/batch",
    summary="Run up to 20 text queries in one request",
    responses=_QUERY_RESPONSES,
    response_model_exclude_none=True,
)
async def recommend_batch(
    payload: BatchRecommendRequest,
    auth: AuthDep,
    engine: QueryEngineDep,
    respond: ResponderDep,
    limits: WorkspaceLimitsDep,
) -> BatchRecommendResponse:
    await limits.consume_queries(len(payload.queries))
    queries = [BatchQuery(q.id, q.query, q.filters) for q in payload.queries]
    recommendations = await engine.recommend_batch(
        queries, auth.tenant, payload.top_k, payload.user_id
    )
    return respond.batch(recommendations, payload.user_id)


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
