import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import Tenant
from app.models.tenant import Plan
from app.services.billing import plans
from app.services.billing.plans import Allowance, allowance, entitled_plan
from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient
from tests.test_ask import _answer
from tests.test_recommend_endpoints import HR_CONFIG, JOBS, REC
from tests.test_reranker import _set_ranking

BILLING = "/api/v1/billing"


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
def billing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    # Small Free limits, so tests need not upload a thousand items.
    monkeypatch.setitem(plans.PLANS, Plan.FREE, Allowance(3, 4, None, llm_features=False))


@pytest.fixture
async def hr(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    return auth


async def _subscribe(
    session_factory: async_sessionmaker[AsyncSession],
    auth: TenantAuth,
    plan: Plan = Plan.PRO,
    status: str | None = "active",
) -> None:
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(auth.tenant_id))
        assert tenant is not None
        tenant.plan, tenant.subscription_status = plan, status
        await session.commit()


def _tenant(**fields: Any) -> Tenant:
    base = {"plan": Plan.FREE, "subscription_status": None, "max_items": None}
    return Tenant(**{**base, "monthly_query_limit": None, "rate_limit_rpm": None, **fields})


# --- Which plan, which limits ---


@pytest.mark.parametrize(
    ("plan", "status", "expected"),
    [
        (Plan.FREE, None, Plan.FREE),
        (Plan.PRO, "active", Plan.PRO),
        (Plan.PRO, "trialing", Plan.PRO),
        (Plan.PRO, "past_due", Plan.PRO),  # grace while Stripe retries
        (Plan.PRO, "canceled", Plan.FREE),
        (Plan.PRO, "unpaid", Plan.FREE),
        (Plan.PRO, None, Plan.FREE),
    ],
)
def test_entitled_plan(plan: Plan, status: str | None, expected: Plan) -> None:
    assert entitled_plan(_tenant(plan=plan, subscription_status=status)) is expected


def test_billing_off_is_unlimited() -> None:
    a = allowance(_tenant())
    assert (a.max_items, a.monthly_queries, a.llm_features) == (None, None, True)


@pytest.mark.usefixtures("billing_on")
def test_admin_overrides_win_over_the_plan() -> None:
    assert allowance(_tenant()).max_items == 3
    overridden = allowance(_tenant(max_items=500, rate_limit_rpm=7))
    assert (overridden.max_items, overridden.monthly_queries, overridden.rate_limit_rpm) == (
        500,
        4,
        7,
    )


# --- Enforced through the API ---


async def test_status_endpoint_with_billing_off(client: AsyncClient, hr: TenantAuth) -> None:
    body = (await client.get(BILLING, headers=hr.headers)).json()

    assert body["enabled"] is False
    assert body["allowance"]["max_items"] is None and body["allowance"]["llm_features"] is True
    assert body["usage"] == {"items": 3, "queries_this_month": 0}
    assert [p["plan"] for p in body["plans"]] == ["FREE", "PRO"]


@pytest.mark.usefixtures("billing_on")
async def test_free_plan_limits(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    extra = {"items": [{"external_id": "job-4", "description": "Go"}], "async": False}
    over = await client.post("/api/v1/items/upload", json=extra, headers=hr.headers)
    queries = [
        await client.post(f"{REC}/by-text", json={"query": "x"}, headers=hr.headers)
        for _ in range(5)
    ]

    assert over.status_code == 403
    assert [q.status_code for q in queries] == [200, 200, 200, 200, 403]
    body = (await client.get(BILLING, headers=hr.headers)).json()
    assert (body["enabled"], body["plan"]) == (True, "FREE")
    assert body["allowance"]["max_items"] == 3
    assert body["usage"] == {"items": 3, "queries_this_month": 4}

    await _subscribe(session_factory, hr)
    assert (
        await client.post("/api/v1/items/upload", json=extra, headers=hr.headers)
    ).status_code == 200


@pytest.mark.usefixtures("billing_on")
async def test_asking_needs_pro(
    client: AsyncClient,
    hr: TenantAuth,
    openai_client: FakeOpenAIClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    openai_client.chat.completions.content = _answer("engineer")
    free = await client.post(f"{REC}/ask", json={"question": "jobs"}, headers=hr.headers)
    await _subscribe(session_factory, hr)
    pro = await client.post(f"{REC}/ask", json={"question": "jobs"}, headers=hr.headers)
    await _subscribe(session_factory, hr, status="canceled")
    lapsed = await client.post(f"{REC}/ask", json={"question": "jobs"}, headers=hr.headers)

    assert free.status_code == 402
    assert free.json()["error"]["code"] == "plan_required"
    assert "Pro plan" in free.json()["error"]["message"]
    assert pro.status_code == 200
    assert lapsed.status_code == 402


@pytest.mark.usefixtures("billing_on")
async def test_llm_reranking_is_skipped_on_free(
    client: AsyncClient,
    hr: TenantAuth,
    openai_client: FakeOpenAIClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _set_ranking(session_factory, hr, llm_rerank=True)
    free = (await client.post(f"{REC}/by-text", json={"query": "a"}, headers=hr.headers)).json()
    calls_on_free = len(openai_client.chat.completions.calls)
    await _subscribe(session_factory, hr)
    pro = (await client.post(f"{REC}/by-text", json={"query": "b"}, headers=hr.headers)).json()

    assert calls_on_free == 0 and free["rerank_tokens"] == 0
    assert len(openai_client.chat.completions.calls) == 1 and pro["rerank_tokens"] > 0
