import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import UserFeedback
from app.services.embedding import openai_embedder
from app.services.recommendation.personalization import Taste, liked_items, normalize
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeVectorStore
from tests.test_recommend_endpoints import HR_CONFIG, JOB1_TEXT, JOBS, REC
from tests.test_reranker import _set_ranking


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def hr(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    # Taste alone, so the tests do not depend on how similar the fake jobs are.
    await _set_ranking(session_factory, auth, personalization=1)
    return auth


async def _react(
    client: AsyncClient, auth: TenantAuth, user_id: str, item: str, feedback_type: str
) -> None:
    query = (
        await client.post(
            f"{REC}/by-text", json={"query": "anything", "user_id": user_id}, headers=auth.headers
        )
    ).json()
    response = await client.post(
        f"{REC}/feedback",
        json={
            "query_id": query["query_id"],
            "external_item_id": item,
            "feedback_type": feedback_type,
        },
        headers=auth.headers,
    )
    assert response.status_code == 201, response.text


async def _top(client: AsyncClient, auth: TenantAuth, **payload: object) -> tuple[str, str]:
    """The first result's external id and the X-Cache header."""
    response = await client.post(
        f"{REC}/by-text", json={"query": JOB1_TEXT, **payload}, headers=auth.headers
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["external_id"], response.headers["X-Cache"]


# --- Through the API ---


async def test_query_leans_toward_what_the_user_liked(
    client: AsyncClient, hr: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await _react(client, hr, "user-42", "job-2", "CLICK")

    assert (await _top(client, hr))[0] == "job-1"
    assert (await _top(client, hr, user_id="user-42"))[0] == "job-2"
    assert len(vector_store.fetches[-1]) == 1  # the one liked item's vector


async def test_results_are_cached_per_user_with_history(
    client: AsyncClient, hr: TenantAuth
) -> None:
    await _react(client, hr, "user-42", "job-2", "APPLY")

    assert (await _top(client, hr))[1] == "MISS"
    assert (await _top(client, hr, user_id="user-42"))[1] == "MISS"
    assert (await _top(client, hr, user_id="user-42"))[1] == "HIT"
    # No history: the anonymous results, from the anonymous cache entry.
    assert await _top(client, hr, user_id="new-user") == ("job-1", "HIT")


async def test_dislikes_are_not_taste(
    client: AsyncClient, hr: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await _react(client, hr, "user-42", "job-2", "THUMBS_DOWN")
    await _react(client, hr, "user-42", "job-3", "IGNORE")
    fetches = len(vector_store.fetches)

    assert (await _top(client, hr, user_id="user-42"))[0] == "job-1"
    assert len(vector_store.fetches) == fetches


async def test_other_users_and_workspaces_do_not_leak(client: AsyncClient, hr: TenantAuth) -> None:
    await _react(client, hr, "user-42", "job-2", "CLICK")
    other = await register_tenant(client, "other@acme.example", "HR", HR_CONFIG)
    await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=other.headers
    )

    assert (await _top(client, hr, user_id="user-7"))[0] == "job-1"
    assert (await _top(client, other, user_id="user-42"))[0] == "job-1"


async def test_by_item_and_batch_are_personalized(client: AsyncClient, hr: TenantAuth) -> None:
    await _react(client, hr, "user-42", "job-3", "PURCHASE")

    similar = (
        await client.post(
            f"{REC}/by-item",
            json={"external_id": "job-1", "user_id": "user-42"},
            headers=hr.headers,
        )
    ).json()
    batch = (
        await client.post(
            f"{REC}/batch",
            json={"queries": [{"id": "a", "query": JOB1_TEXT}], "user_id": "user-42"},
            headers=hr.headers,
        )
    ).json()

    assert [r["external_id"] for r in similar["results"]][0] == "job-3"
    assert "job-1" not in [r["external_id"] for r in similar["results"]]
    assert batch["results"]["a"][0]["external_id"] == "job-3"


@pytest.mark.parametrize(
    "ranking", [{"personalization": 0}, {"enabled": False, "personalization": 1}]
)
async def test_personalization_can_be_turned_off(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
    ranking: dict[str, object],
) -> None:
    await _react(client, hr, "user-42", "job-2", "CLICK")
    await _set_ranking(session_factory, hr, **ranking)

    assert (await _top(client, hr, user_id="user-42"))[0] == "job-1"
    assert vector_store.fetches == []


async def test_history_is_the_recent_window_of_embedded_items(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _react(client, hr, "user-42", "job-2", "CLICK")
    await _react(client, hr, "user-42", "job-3", "CLICK")
    tenant_id = uuid.UUID(hr.tenant_id)
    async with session_factory() as session:
        await session.execute(
            update(UserFeedback)
            .where(UserFeedback.external_item_id == "job-3")
            .values(created_at=datetime.now(UTC) - timedelta(days=31))
        )
        await session.commit()
        recent = await liked_items(session, tenant_id, "user-42")
    await client.delete("/api/v1/items/job-2", headers=hr.headers)
    async with session_factory() as session:
        after_delete = await liked_items(session, tenant_id, "user-42")

    assert len(recent) == 1  # job-2 only; job-3 is outside the window
    assert after_delete == []


# --- Blending ---


def test_taste_blending() -> None:
    query, liked = [1.0, 0.0], [0.0, 2.0]
    assert Taste.from_vectors([], 0.5) is None
    assert Taste.from_vectors([liked], 0.0).apply(query) == pytest.approx([1.0, 0.0])  # type: ignore[union-attr]
    assert Taste.from_vectors([liked], 1.0).apply(query) == pytest.approx([0.0, 1.0])  # type: ignore[union-attr]
    halfway = Taste.from_vectors([liked, [0.0, 1.0]], 0.5).apply([3.0, 0.0])  # type: ignore[union-attr]
    assert halfway == pytest.approx(normalize([1.0, 1.0]))
