"""HTTP metrics, calls to external services, and the embedding backlog gauge."""

import uuid
from datetime import timedelta

import httpx
import openai
import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import call_outcome, track_external_call
from app.middleware.metrics import route_template
from app.models.base import utcnow
from app.models.item import EmbeddingStatus, Item
from app.services.embedding import openai_embedder
from app.services.embedding.openai_embedder import EmbeddingUnavailableError, OpenAIEmbedder
from tests.conftest import TenantAuth
from tests.fakes import TEST_DIMENSION, FakeOpenAIClient

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/embeddings")


def sample(name: str, **labels: str) -> float:
    """Current value of a metric; counters are process-wide, so tests compare deltas."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def http_count(method: str, route: str, status: str) -> float:
    return sample("http_requests_total", method=method, route=route, status=status)


def scraped_value(body: str, metric: str) -> float:
    line = next(line for line in body.splitlines() if line.startswith(f"{metric} "))
    return float(line.split()[1])


# --- HTTP ---


async def test_requests_are_counted_by_route_template(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    route = "/api/v1/items/{external_id}"
    before = http_count("GET", route, "404")
    timed = sample("http_request_duration_seconds_count", method="GET", route=route)

    await client.get("/api/v1/items/job-1", headers=hr_tenant.headers)
    await client.get("/api/v1/items/job-2", headers=hr_tenant.headers)

    # Both concrete paths land on one series, so label values stay bounded.
    assert http_count("GET", route, "404") == before + 2
    assert http_count("GET", "/api/v1/items/job-1", "404") == 0
    assert sample("http_request_duration_seconds_count", method="GET", route=route) == timed + 2


@pytest.mark.parametrize(
    ("route", "path", "expected"),
    [
        # Lazily included routers: the route path is relative to the router prefix.
        ("/items/{external_id}", "/api/v1/items/job-1", "/api/v1/items/{external_id}"),
        ("", "/api/v1/items", "/api/v1/items"),
        ("/items", "/api/v1/items/", "/api/v1/items"),
        # Routes that already carry their full path.
        ("/api/v1/items/{external_id}", "/api/v1/items/job-1", "/api/v1/items/{external_id}"),
        ("/health", "/health", "/health"),
    ],
)
def test_route_template(route: str, path: str, expected: str) -> None:
    class Route:
        pass

    matched = Route()
    matched.path = route  # type: ignore[attr-defined]
    assert route_template({"route": matched, "path": path}) == expected
    assert route_template({"path": path}) == "unmatched"


async def test_unknown_paths_share_one_label(client: AsyncClient) -> None:
    before = http_count("GET", "unmatched", "404")

    await client.get("/no-such-page")
    await client.get("/wp-admin/setup.php")

    assert http_count("GET", "unmatched", "404") == before + 2


async def test_auth_failures_are_counted(client: AsyncClient) -> None:
    before = http_count("GET", "/api/v1/items", "401")

    response = await client.get("/api/v1/items")

    assert response.status_code == 401
    assert http_count("GET", "/api/v1/items", "401") == before + 1


# --- External services ---


def status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("error", response=httpx.Response(code, request=_REQUEST), body=None)


def test_call_outcomes() -> None:
    assert call_outcome(status_error(openai.RateLimitError, 429)) == "rate_limited"
    assert call_outcome(openai.APITimeoutError(request=_REQUEST)) == "timeout"
    assert call_outcome(openai.APIConnectionError(request=_REQUEST)) == "connection_error"
    assert call_outcome(status_error(openai.BadRequestError, 400)) == "client_error"
    assert call_outcome(status_error(openai.InternalServerError, 500)) == "error"
    assert call_outcome(TimeoutError()) == "timeout"
    assert call_outcome(RuntimeError("anything else")) == "error"


def test_track_external_call_records_and_reraises() -> None:
    labels = {"service": "test", "operation": "op"}
    ok = sample("external_calls_total", **labels, outcome="ok")
    timeouts = sample("external_calls_total", **labels, outcome="timeout")

    with track_external_call("test", "op"):
        pass
    with pytest.raises(TimeoutError), track_external_call("test", "op"):
        raise TimeoutError

    assert sample("external_calls_total", **labels, outcome="ok") == ok + 1
    assert sample("external_calls_total", **labels, outcome="timeout") == timeouts + 1
    assert sample("external_call_duration_seconds_count", **labels) >= 2


async def test_openai_attempts_are_counted_one_by_one(
    monkeypatch: pytest.MonkeyPatch, openai_client: FakeOpenAIClient
) -> None:
    labels = {"service": "openai", "operation": "embeddings"}
    ok = sample("external_calls_total", **labels, outcome="ok")
    failed = sample("external_calls_total", **labels, outcome="connection_error")
    embedder = OpenAIEmbedder(openai_client, dimension=TEST_DIMENSION)  # type: ignore[arg-type]

    await embedder.embed_text("python developer")
    monkeypatch.setattr(openai_embedder, "wait_random_exponential", lambda **_: lambda _: 0)
    openai_client.embeddings.down = True
    with pytest.raises(EmbeddingUnavailableError):
        await embedder.embed_text("go developer")

    assert sample("external_calls_total", **labels, outcome="ok") == ok + 1
    # Every retry is visible, so throttling shows up before requests start failing.
    retries = openai_embedder.RETRY_ATTEMPTS
    assert sample("external_calls_total", **labels, outcome="connection_error") == failed + retries


# --- Embedding backlog ---


async def test_oldest_pending_item_age(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = (await client.get("/metrics")).text
    assert scraped_value(body, "oldest_pending_item_age_seconds") == 0

    async with session_factory() as session:
        for external_id, age, status in [
            ("stuck", timedelta(minutes=40), EmbeddingStatus.PENDING),
            ("fresh", timedelta(seconds=5), EmbeddingStatus.PENDING),
            ("old-but-done", timedelta(days=2), EmbeddingStatus.DONE),
        ]:
            session.add(
                Item(
                    tenant_id=uuid.UUID(hr_tenant.tenant_id),
                    external_id=external_id,
                    raw_data={"description": external_id},
                    embedding_status=status,
                    created_at=utcnow() - age,
                )
            )
        await session.commit()

    body = (await client.get("/metrics")).text

    age = scraped_value(body, "oldest_pending_item_age_seconds")
    assert 40 * 60 <= age < 41 * 60
    assert f'items_total{{status="PENDING",tenant_id="{hr_tenant.tenant_id}"}} 2.0' in body
