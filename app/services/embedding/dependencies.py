"""FastAPI dependencies for the embedding pipeline. Tests override `get_embedder` and
`get_vector_store` to avoid calling OpenAI and Pinecone."""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_session_factory
from app.core.redis_client import get_redis
from app.services.embedding.openai_embedder import OpenAIEmbedder, get_openai_client
from app.services.embedding.pinecone_service import PineconeService, get_pinecone_service
from app.services.embedding.pipeline import EmbeddingPipeline


def get_embedder(redis: Annotated[Redis, Depends(get_redis)]) -> OpenAIEmbedder:
    return OpenAIEmbedder(get_openai_client(), redis)


def get_vector_store() -> PineconeService:
    return get_pinecone_service()


VectorStoreDep = Annotated[PineconeService, Depends(get_vector_store)]


def get_pipeline(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    embedder: Annotated[OpenAIEmbedder, Depends(get_embedder)],
    vector_store: VectorStoreDep,
) -> EmbeddingPipeline:
    return EmbeddingPipeline(session_factory, embedder, vector_store)


PipelineDep = Annotated[EmbeddingPipeline, Depends(get_pipeline)]
