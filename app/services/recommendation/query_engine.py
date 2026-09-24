"""Answers recommendation queries: by free text, by an existing item, or by a profile.

Every query follows the same path: validate filters -> check the result cache -> get a
query vector (embed text, or reuse the item's stored vector) -> Pinecone similarity
search -> rank and format. Upstream failures become 503s; nothing partial is returned.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.tracing import clip, traced
from app.core.usage import track_embedding_usage
from app.models.item import EmbeddingStatus, Item
from app.models.recommendation_log import QueryType
from app.models.tenant import Tenant
from app.models.token_usage import UsageSource
from app.services.embedding.openai_embedder import (
    EmbeddingInputError,
    EmbeddingUnavailableError,
    OpenAIEmbedder,
)
from app.services.embedding.pinecone_service import (
    QUERY_TIMEOUT_SECONDS,
    PineconeService,
    VectorStoreQueryError,
    VectorStoreTimeoutError,
    VectorStoreUnavailableError,
)
from app.services.embedding.text_builder import TextBuilder
from app.services.recommendation.cache import RecommendationCache
from app.services.recommendation.filter_builder import FilterBuilder
from app.services.recommendation.result_formatter import ResultFormatter
from app.services.token_usage import record_embedding_usage

logger = logging.getLogger(__name__)

CacheStatus = Literal["HIT", "MISS", "BYPASS"]
RETRY_AFTER_SECONDS = 5

Matches = list[dict[str, Any]]


@dataclass
class Recommendation:
    query_type: QueryType
    query_input: dict[str, Any]
    filters: dict[str, Any]
    results: list[dict[str, Any]]
    cache_status: CacheStatus
    # OpenAI tokens spent embedding this query (0 on cache hits and by-item queries).
    embedding_tokens: int = 0


@dataclass
class BatchQuery:
    id: str
    query: str
    filters: dict[str, Any] | None = None


def _unavailable(exc: Exception) -> ServiceUnavailableError:
    retry = {"Retry-After": str(RETRY_AFTER_SECONDS)}
    if isinstance(exc, VectorStoreTimeoutError):
        return ServiceUnavailableError(
            f"Vector search timed out after {QUERY_TIMEOUT_SECONDS:.0f}s. No results were "
            "returned; retry in a few seconds.",
            headers=retry,
        )
    if isinstance(exc, EmbeddingUnavailableError):
        return ServiceUnavailableError(
            "Embedding service is unavailable; retry in a few seconds.", headers=retry
        )
    return ServiceUnavailableError(f"Vector search is unavailable: {exc}", headers=retry)


@contextmanager
def _upstream_errors() -> Iterator[None]:
    """Map OpenAI/Pinecone failures to API errors, logging why a request got a 503."""
    try:
        yield
    except (VectorStoreUnavailableError, EmbeddingUnavailableError) as exc:
        logger.warning("Recommendation unavailable (%s): %s", type(exc).__name__, exc)
        raise _unavailable(exc) from exc
    except VectorStoreQueryError as exc:
        raise BadRequestError(
            f"Pinecone rejected the query; check that filter values match the stored types: {exc}"
        ) from exc
    except EmbeddingInputError as exc:
        raise BadRequestError(f"The query text was rejected by the embedding model: {exc}") from exc


def _trace_inputs(**fields: str) -> Any:
    """Inputs for a recommendation trace: tenant id plus the named call arguments."""

    def build(args: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"tenant_id": str(args["tenant"].id)}
        for label, arg in fields.items():
            value = args.get(arg)
            if value is not None:
                out[label] = clip(value) if isinstance(value, str) else value
        return out

    return build


def _trace_recommendation(rec: Recommendation) -> dict[str, Any]:
    return {
        "cache": rec.cache_status,
        "embedding_tokens": rec.embedding_tokens,
        "results": [
            {
                "rank": r["rank"],
                "external_id": r["external_id"],
                "score": r["score"],
                "label": r["score_label"],
            }
            for r in rec.results
        ],
    }


class QueryEngine:
    def __init__(
        self,
        session: AsyncSession,
        embedder: OpenAIEmbedder,
        vector_store: PineconeService,
        cache: RecommendationCache,
        *,
        formatter: ResultFormatter | None = None,
        text_builder: TextBuilder | None = None,
        filter_builder: FilterBuilder | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._vector_store = vector_store
        self._cache = cache
        self._formatter = formatter or ResultFormatter()
        self._text_builder = text_builder or TextBuilder()
        self._filter_builder = filter_builder or FilterBuilder()

    # --- Query types ---

    @traced(
        "recommend.by_text",
        inputs=_trace_inputs(query="query_text", top_k="top_k", filters="filters"),
        outputs=_trace_recommendation,
    )
    async def recommend_by_text(
        self,
        query_text: str,
        tenant: Tenant,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        include_raw_data: bool = False,
    ) -> Recommendation:
        return await self._recommend(
            tenant,
            QueryType.TEXT,
            {"query": query_text},
            top_k,
            filters,
            include_raw_data,
            lambda pinecone_filter: self._search_text(query_text, tenant, top_k, pinecone_filter),
        )

    @traced(
        "recommend.by_item",
        inputs=_trace_inputs(external_id="external_id", top_k="top_k", filters="filters"),
        outputs=_trace_recommendation,
    )
    async def recommend_by_item_id(
        self,
        external_id: str,
        tenant: Tenant,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        include_raw_data: bool = False,
    ) -> Recommendation:
        """Items similar to an existing item. The item itself is never in the results."""
        item = await self._embedded_item(tenant, external_id)
        return await self._recommend(
            tenant,
            QueryType.ITEM_ID,
            {"external_id": external_id},
            top_k,
            filters,
            include_raw_data,
            lambda pinecone_filter: self._search_similar(item, tenant, top_k, pinecone_filter),
        )

    @traced(
        "recommend.by_profile",
        inputs=_trace_inputs(profile="profile", top_k="top_k", filters="filters"),
        outputs=_trace_recommendation,
    )
    async def recommend_by_profile(
        self,
        profile: dict[str, Any],
        tenant: Tenant,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        include_raw_data: bool = False,
    ) -> Recommendation:
        """E.g. HR: a candidate's skills and experience in, matching jobs out."""
        text = self._text_builder.build_profile_text(profile, tenant.domain_config)
        if not text:
            raise BadRequestError("Profile has no text to match on; every field is empty")
        return await self._recommend(
            tenant,
            QueryType.PROFILE,
            {"profile": profile},
            top_k,
            filters,
            include_raw_data,
            lambda pinecone_filter: self._search_text(text, tenant, top_k, pinecone_filter),
        )

    @traced(
        "recommend.batch",
        inputs=lambda a: {
            "tenant_id": str(a["tenant"].id),
            "top_k": a.get("top_k"),
            "queries": [{"id": q.id, "query": clip(q.query)} for q in a["queries"]],
        },
        outputs=lambda recs: {qid: _trace_recommendation(r) for qid, r in recs.items()},
    )
    async def recommend_batch(
        self, queries: list[BatchQuery], tenant: Tenant, top_k: int = 10
    ) -> dict[str, Recommendation]:
        """Several text queries at once: one OpenAI call for all uncached texts, then the
        Pinecone searches run concurrently. Any upstream failure fails the whole batch."""
        pinecone_filters: dict[str, dict[str, Any]] = {}
        for query in queries:
            try:
                pinecone_filters[query.id] = self._filter_builder.build_pinecone_filter(
                    query.filters, tenant.domain_config
                )
            except BadRequestError as exc:
                raise BadRequestError(
                    f"Invalid filters in query '{query.id}'", details=exc.details
                ) from exc

        keys = {
            q.id: self._cache.key(
                tenant.id, {"type": QueryType.TEXT, "query": q.query}, pinecone_filters[q.id], top_k
            )
            for q in queries
        }
        cached = await asyncio.gather(*(self._cache.get(keys[q.id]) for q in queries))
        hits = {q.id: hit for q, hit in zip(queries, cached, strict=True) if hit is not None}
        misses = [q for q in queries if q.id not in hits]

        fresh: dict[str, list[dict[str, Any]]] = {}
        query_tokens: dict[str, int] = {}
        if misses:
            with track_embedding_usage() as usage:
                try:
                    with _upstream_errors():
                        vectors = await self._embedder.embed_batch([q.query for q in misses])
                        all_matches = await asyncio.gather(
                            *(
                                self._vector_store.query(
                                    tenant.id,
                                    top_k=top_k,
                                    vector=vector,
                                    filter=pinecone_filters[q.id],
                                )
                                for q, vector in zip(misses, vectors, strict=True)
                            )
                        )
                finally:
                    await record_embedding_usage(self._session, tenant.id, UsageSource.QUERY, usage)
            # One OpenAI call covers all misses; split its tokens so the parts add up.
            base, extra = divmod(usage.tokens, len(misses))
            query_tokens = {q.id: base + (i < extra) for i, q in enumerate(misses)}
            for query, matches in zip(misses, all_matches, strict=True):
                fresh[query.id] = self._formatter.format_results(matches, tenant)
            await asyncio.gather(*(self._cache.set(keys[q_id], r) for q_id, r in fresh.items()))

        return {
            q.id: Recommendation(
                query_type=QueryType.TEXT,
                query_input={"query": q.query},
                filters=q.filters or {},
                results=hits[q.id] if q.id in hits else fresh[q.id],
                cache_status="HIT" if q.id in hits else "MISS",
                embedding_tokens=query_tokens.get(q.id, 0),
            )
            for q in queries
        }

    # --- Shared path ---

    async def _recommend(
        self,
        tenant: Tenant,
        query_type: QueryType,
        query_input: dict[str, Any],
        top_k: int,
        filters: dict[str, Any] | None,
        include_raw_data: bool,
        search: Callable[[dict[str, Any]], Awaitable[Matches]],
    ) -> Recommendation:
        pinecone_filter = self._filter_builder.build_pinecone_filter(filters, tenant.domain_config)

        def done(
            results: list[dict[str, Any]], status: CacheStatus, tokens: int = 0
        ) -> Recommendation:
            return Recommendation(query_type, query_input, filters or {}, results, status, tokens)

        # raw_data can be large and changes often; those responses are never cached.
        key = None
        if not include_raw_data:
            key = self._cache.key(
                tenant.id, {"type": query_type, **query_input}, pinecone_filter, top_k
            )
            if (cached := await self._cache.get(key)) is not None:
                return done(cached, "HIT")

        with track_embedding_usage() as usage:
            try:
                with _upstream_errors():
                    matches = await search(pinecone_filter)
            finally:
                # Tokens spent before a Pinecone failure still count.
                await record_embedding_usage(self._session, tenant.id, UsageSource.QUERY, usage)

        raw_data = await self._raw_data(tenant, matches) if include_raw_data else None
        results = self._formatter.format_results(matches, tenant, include_raw_data, raw_data)
        if key is None:
            return done(results, "BYPASS", usage.tokens)
        await self._cache.set(key, results)
        return done(results, "MISS", usage.tokens)

    async def _search_text(
        self, text: str, tenant: Tenant, top_k: int, pinecone_filter: dict[str, Any]
    ) -> Matches:
        vector = await self._embedder.embed_text(text)
        return await self._vector_store.query(
            tenant.id, top_k=top_k, vector=vector, filter=pinecone_filter
        )

    async def _search_similar(
        self, item: Item, tenant: Tenant, top_k: int, pinecone_filter: dict[str, Any]
    ) -> Matches:
        # Query by the stored vector's id (one round trip); ask for one extra to drop itself.
        matches = await self._vector_store.query(
            tenant.id, top_k=top_k + 1, id=item.pinecone_id, filter=pinecone_filter
        )
        return [m for m in matches if m["id"] != item.pinecone_id][:top_k]

    async def _embedded_item(self, tenant: Tenant, external_id: str) -> Item:
        item = await self._session.scalar(
            select(Item).where(Item.tenant_id == tenant.id, Item.external_id == external_id)
        )
        if item is None:
            raise NotFoundError(f"Item '{external_id}' not found")
        if item.embedding_status is not EmbeddingStatus.DONE or not item.pinecone_id:
            raise ConflictError(
                f"Item '{external_id}' is not embedded yet (status {item.embedding_status})"
            )
        return item

    async def _raw_data(self, tenant: Tenant, matches: Matches) -> dict[str, dict[str, Any]]:
        external_ids = [
            str((m.get("metadata") or {}).get("external_id") or m["id"]) for m in matches
        ]
        if not external_ids:
            return {}
        rows = await self._session.execute(
            select(Item.external_id, Item.raw_data).where(
                Item.tenant_id == tenant.id, Item.external_id.in_(external_ids)
            )
        )
        return dict(rows.tuples().all())
