"""LangSmith tracing: which steps are traced and what leaves the process."""

import os
import re
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest
from langsmith import tracing_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import tracing
from app.core.config import settings
from app.models import EmbeddingStatus, Tenant
from app.services.embedding import openai_embedder
from app.services.embedding.openai_embedder import OpenAIEmbedder
from app.services.embedding.pipeline import EmbeddingPipeline
from app.services.item_service import ItemService
from app.services.recommendation.cache import RecommendationCache
from app.services.recommendation.query_engine import QueryEngine
from tests.conftest import HR_CONFIG
from tests.fakes import FakeVectorStore

JOBS: list[dict[str, Any]] = [
    {"external_id": "job-1", "title": "Backend Engineer", "description": "Python APIs"},
    {"external_id": "job-2", "title": "Data Scientist", "description": "Ranking models"},
]


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


def sent_payloads(client: MagicMock) -> str:
    """Everything the traced code handed to the LangSmith client, as text."""
    return " ".join(repr(call) for call in client.mock_calls)


async def test_tracing_is_off_in_tests() -> None:
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_configure_tracing_needs_flag_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", None)
    assert tracing.configure_tracing() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"

    from pydantic import SecretStr

    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", SecretStr("lsv2_test"))
    monkeypatch.setattr(settings, "LANGSMITH_PROJECT", "reco-test")
    try:
        assert tracing.configure_tracing() is True
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_PROJECT"] == "reco-test"
    finally:
        monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
        tracing.configure_tracing()
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_langchain_variable_names_are_accepted() -> None:
    from app.core.config import Settings

    parsed = Settings(  # type: ignore[call-arg]
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://x",
        SECRET_KEY="k" * 40,
        LANGCHAIN_TRACING_V2="true",
        LANGCHAIN_API_KEY="lsv2_abc",
        LANGCHAIN_PROJECT="my-project",
    )
    assert parsed.LANGSMITH_TRACING is True
    assert parsed.LANGSMITH_API_KEY is not None
    assert parsed.LANGSMITH_PROJECT == "my-project"


async def test_recommendation_is_traced_without_vectors(
    session_factory: async_sessionmaker[AsyncSession],
    pipeline: EmbeddingPipeline,
    embedder: OpenAIEmbedder,
    vector_store: FakeVectorStore,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    async with session_factory() as session:
        tenant = Tenant(name="Acme", email="a@x.io", domain_type="HR", domain_config=HR_CONFIG)
        session.add(tenant)
        await session.commit()
        items = await ItemService(session, tenant).upsert_items(
            JOBS, status=EmbeddingStatus.PROCESSING
        )
        client = MagicMock()
        with tracing_context(enabled=True, client=client):
            await pipeline.process_items(session, items, tenant)
            engine = QueryEngine(session, embedder, vector_store, RecommendationCache(redis))  # type: ignore[arg-type]
            await engine.recommend_by_text("senior python developer", tenant, top_k=2)

    payloads = sent_payloads(client)
    for step in ("embedding_pipeline", "recommend.by_text", "embed", "openai.embeddings"):
        assert f"'{step}'" in payloads, f"{step} was not traced"
    assert "senior python developer" in payloads
    assert str(tenant.id) in payloads
    # A vector is 8 floats in tests (1536 in production); none may appear in a trace.
    assert not re.search(r"\[(?:0\.\d+, ){7}0\.\d+\]", payloads)
    assert "'dimensions': 8" in payloads
    # Token usage is reported on the OpenAI call, so LangSmith can show tokens and cost.
    assert "'total_tokens':" in payloads
    assert "'ls_model_name': 'text-embedding-3-small'" in payloads
