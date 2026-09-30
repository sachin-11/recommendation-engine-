import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import RankingVariant
from app.schemas.tenant import RankingConfig
from app.services.embedding import openai_embedder
from app.services.recommendation import experiment
from app.services.recommendation.experiment import (
    _bucket,
    assign_variant,
    two_proportion_p_value,
    wilson_interval,
)
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeVectorStore
from tests.test_recommend_endpoints import HR_CONFIG, JOB1_TEXT, JOBS, REC
from tests.test_reranker import _seed_stats, _set_ranking, _variant

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def hr(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    return auth


def _user_in(variant: RankingVariant, tenant_id: uuid.UUID, share: float = 0.5) -> str:
    """A user id the hash puts in `variant` at this control share."""
    for n in range(1000):
        user = f"user-{n}"
        if (_bucket(tenant_id, user) < share) == (variant is RankingVariant.CONTROL):
            return user
    raise AssertionError("no user found")


# --- Assignment ---


def test_no_test_and_disabled_ranking() -> None:
    assert assign_variant(RankingConfig(), TENANT, "u") is RankingVariant.RERANKED
    assert assign_variant(RankingConfig(control_share=1), TENANT, "u") is RankingVariant.CONTROL
    disabled = RankingConfig(enabled=False, control_share=0)
    assert assign_variant(disabled, TENANT, "u") is RankingVariant.CONTROL


def test_users_keep_their_variant_and_split_by_share() -> None:
    config = RankingConfig(control_share=0.3)
    users = [f"user-{n}" for n in range(4000)]
    first = [assign_variant(config, TENANT, u) for u in users]

    assert first == [assign_variant(config, TENANT, u) for u in users]
    share = first.count(RankingVariant.CONTROL) / len(users)
    assert 0.27 < share < 0.33
    # Another workspace splits its users independently.
    other = [assign_variant(config, uuid.uuid4(), u) for u in users]
    assert other != first


def test_anonymous_requests_are_assigned_per_query(monkeypatch: pytest.MonkeyPatch) -> None:
    config = RankingConfig(control_share=0.5)
    monkeypatch.setattr(experiment.random, "random", lambda: 0.49)
    assert assign_variant(config, TENANT, None) is RankingVariant.CONTROL
    monkeypatch.setattr(experiment.random, "random", lambda: 0.51)
    assert assign_variant(config, TENANT, None) is RankingVariant.RERANKED


# --- Statistics ---


def test_wilson_interval() -> None:
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(5, 10)  # type: ignore[misc]
    assert (low, high) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))
    low, high = wilson_interval(0, 20)  # type: ignore[misc]
    assert low == 0.0 and 0 < high < 0.2
    assert wilson_interval(50, 10) == wilson_interval(10, 10)  # capped at the trials


def test_two_proportion_p_value() -> None:
    assert two_proportion_p_value(1, 0, 1, 10) is None
    assert two_proportion_p_value(0, 10, 0, 10) is None  # no variance at all
    assert two_proportion_p_value(60, 1000, 30, 1000) == pytest.approx(0.0012, abs=1e-4)
    assert two_proportion_p_value(30, 1000, 31, 1000) > 0.8  # type: ignore[operator]


# --- Through the API ---


async def test_control_gets_similarity_order_without_personalization(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid.UUID(hr.tenant_id)
    await _seed_stats(session_factory, hr, {"job-1": 50, "job-2": 900, "job-3": 50})
    await _set_ranking(session_factory, hr, engagement=5, personalization=1, control_share=0.5)
    control, reranked = (_user_in(v, tenant_id) for v in RankingVariant)

    served = {}
    for user in (control, reranked):
        body = (
            await client.post(
                f"{REC}/by-text",
                json={"query": JOB1_TEXT, "top_k": 2, "user_id": user},
                headers=hr.headers,
            )
        ).json()
        served[user] = (
            body["results"][0]["external_id"],
            await _variant(session_factory, body["query_id"]),
            vector_store.queries[-1]["top_k"],
        )

    assert served[control] == ("job-1", RankingVariant.CONTROL, 2)
    assert served[reranked][:2] == ("job-2", RankingVariant.RERANKED)


async def test_experiment_report(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_id = uuid.UUID(hr.tenant_id)
    await _set_ranking(session_factory, hr, control_share=0.5)
    control, reranked = (_user_in(v, tenant_id) for v in RankingVariant)

    async def query_and_react(user: str, feedback: list[str]) -> None:
        body = (
            await client.post(
                f"{REC}/by-text",
                json={"query": JOB1_TEXT, "top_k": 3, "user_id": user},
                headers=hr.headers,
            )
        ).json()
        for feedback_type in feedback:
            response = await client.post(
                f"{REC}/feedback",
                json={
                    "query_id": body["query_id"],
                    "external_item_id": "job-1",
                    "feedback_type": feedback_type,
                },
                headers=hr.headers,
            )
            assert response.status_code == 201, response.text

    await query_and_react(control, ["CLICK"])
    await query_and_react(control, [])
    await query_and_react(reranked, ["CLICK", "APPLY", "IGNORE"])

    response = await client.get("/api/v1/analytics/ranking-experiment", headers=hr.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["control_share"] == 0.5
    by_variant = {v["variant"]: v for v in body["variants"]}
    c, r = by_variant["control"], by_variant["reranked"]
    assert (c["queries"], c["impressions"], c["engagement"], c["conversions"]) == (2, 6, 1, 0)
    assert (r["queries"], r["impressions"], r["engagement"], r["conversions"]) == (1, 3, 1, 1)
    assert r["negatives"] == 1
    assert c["engagement_rate"]["rate"] == pytest.approx(1 / 6)
    assert c["engagement_rate"]["low"] < 1 / 6 < c["engagement_rate"]["high"]
    assert body["comparison"]["engagement_lift"] == pytest.approx((1 / 3) / (1 / 6) - 1)
    assert 0 < body["comparison"]["engagement_p_value"] <= 1
    assert body["comparison"]["conversion_lift"] is None  # control has no conversions


async def test_experiment_report_without_data(client: AsyncClient) -> None:
    auth = await register_tenant(client, "empty@acme.example")

    body = (await client.get("/api/v1/analytics/ranking-experiment", headers=auth.headers)).json()

    assert body["control_share"] == 0
    assert all(v["impressions"] == 0 for v in body["variants"])
    assert all(v["engagement_rate"]["rate"] is None for v in body["variants"])
    assert body["comparison"] == {
        "engagement_lift": None,
        "engagement_p_value": None,
        "conversion_lift": None,
        "conversion_p_value": None,
    }
