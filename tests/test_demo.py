"""The public demo: a read-only session in the demo workspace, without a password."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import User
from app.models.base import utcnow
from app.models.user import Role
from scripts.demo_workspace import GOLDEN, golden_set
from tests.conftest import TenantAuth

DEMO = "/api/v1/auth/demo"
DEMO_EMAIL = "visitor@demo.example"


@pytest.fixture
async def demo_viewer(
    hr_tenant: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> TenantAuth:
    """The HR workspace stands in for the demo workspace, with a VIEWER visitor."""
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    monkeypatch.setattr(settings, "DEMO_EMAIL", DEMO_EMAIL)
    async with session_factory() as session:
        session.add(
            User(
                tenant_id=uuid.UUID(hr_tenant.tenant_id),
                email=DEMO_EMAIL,
                name="Demo visitor",
                role=Role.VIEWER,
                email_verified_at=utcnow(),
            )
        )
        await session.commit()
    return hr_tenant


async def test_demo_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEMO_ENABLED", False)
    assert (await client.get(DEMO)).json() == {"available": False}
    response = await client.post(DEMO)
    assert response.status_code == 404


async def test_demo_session_is_a_short_read_only_viewer(
    client: AsyncClient, demo_viewer: TenantAuth
) -> None:
    assert (await client.get(DEMO)).json() == {"available": True}

    response = await client.post(DEMO)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant"]["id"] == demo_viewer.tenant_id
    assert (body["tenant"]["role"], body["tenant"]["is_demo"]) == ("VIEWER", True)
    expires = datetime.fromisoformat(body["expires_at"])
    assert expires - datetime.now(UTC) < timedelta(minutes=settings.DEMO_SESSION_MINUTES + 1)

    visitor = {"X-API-Key": body["api_key"]}
    search = await client.post(
        "/api/v1/recommend/by-text", json={"query": "python"}, headers=visitor
    )
    assert search.status_code == 200
    upload = await client.post(
        "/api/v1/items/upload",
        json={"items": [{"external_id": "x", "description": "y"}]},
        headers=visitor,
    )
    assert upload.status_code == 403
    assert (
        await client.post("/api/v1/me/api-keys", json={"name": "k"}, headers=visitor)
    ).status_code == 403

    # Everyone else is not in the demo.
    me = (await client.get("/api/v1/me", headers=demo_viewer.headers)).json()
    assert me["is_demo"] is False


async def test_demo_never_hands_out_a_stronger_role(
    client: AsyncClient, demo_viewer: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pointing DEMO_EMAIL at the workspace owner must not give visitors the owner's rights.
    monkeypatch.setattr(settings, "DEMO_EMAIL", "hr@acme.example")
    assert (await client.post(DEMO)).status_code == 404


async def test_demo_sessions_are_rate_limited_per_client(
    client: AsyncClient, demo_viewer: TenantAuth
) -> None:
    codes = [(await client.post(DEMO)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_demo_golden_set_grades_real_jobs() -> None:
    jobs = json.loads(Path("scripts/demo_jobs.json").read_text(encoding="utf-8"))
    ids = {job["external_id"] for job in jobs}

    queries = golden_set(jobs)

    assert len(queries) == len(GOLDEN)
    for query in queries:
        assert query.relevant, query.query
        assert set(query.relevant) <= ids
        assert 3 in query.relevant.values()
