"""FastAPI dependencies for recommendation queries. Tests override `get_query_embedder`."""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.services.embedding.dependencies import VectorStoreDep
from app.services.embedding.openai_embedder import OpenAIEmbedder, get_openai_client
from app.services.recommendation.cache import RecommendationCache
from app.services.recommendation.query_engine import QueryEngine

# A user is waiting on these calls: fail fast rather than retry for a minute.
QUERY_EMBED_RETRY_ATTEMPTS = 2
QUERY_EMBED_TIMEOUT_SECONDS = 5.0


def get_query_embedder(redis: Annotated[Redis, Depends(get_redis)]) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        get_openai_client(),
        redis,
        retry_attempts=QUERY_EMBED_RETRY_ATTEMPTS,
        request_timeout=QUERY_EMBED_TIMEOUT_SECONDS,
    )


def get_query_engine(
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    embedder: Annotated[OpenAIEmbedder, Depends(get_query_embedder)],
    vector_store: VectorStoreDep,
) -> QueryEngine:
    return QueryEngine(session, embedder, vector_store, RecommendationCache(redis))


QueryEngineDep = Annotated[QueryEngine, Depends(get_query_engine)]
