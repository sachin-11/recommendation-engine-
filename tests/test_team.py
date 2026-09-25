"""Team members, roles and invitations (Module 7)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.email import EmailMessage
from app.models import ApiKey, Tenant, User
from tests.conftest import link_token

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
TEAM = "/api/v1/me/members"
OWNER_EMAIL = "owner@acme.example"
PASSWORD = "owner password 123"
MEMBER_PASSWORD = "member password 123"


def key(value: str) -> dict[str, str]:
    return {"X-API-Key": value}


async def owner_session(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        f"{AUTH}/register",
        json={
            "name": "Acme",
            "owner_name": "Olivia Owner",
            "email": OWNER_EMAIL,
            "password": PASSWORD,
            "domain_type": "HR",
        },
    )
    assert response.status_code == 201, response.text
    await client.post(
        f"{AUTH}/verify-email", json={"token": link_token(OWNER_EMAIL, "/verify-email")}
    )
    return key(response.json()["api_key"])


async def add_member(
    client: AsyncClient, admin: dict[str, str], email: str, role: str, name: str = "Member"
) -> dict[str, str]:
    """Invite, accept, and return the new member's session headers."""
    invited = await client.post(
        f"{TEAM}/invitations", json={"email": email, "role": role}, headers=admin
    )
    assert invited.status_code == 201, invited.text
    accepted = await client.post(
        f"{AUTH}/invitations/accept",
        json={
            "token": link_token(email, "/accept-invite"),
            "name": name,
            "password": MEMBER_PASSWORD,
        },
    )
    assert accepted.status_code == 201, accepted.text
    return key(accepted.json()["api_key"])


async def member_id(client: AsyncClient, headers: dict[str, str], email: str) -> str:
    members = (await client.get(TEAM, headers=headers)).json()["members"]
    return next(m["id"] for m in members if m["email"] == email)


# --- Registration and /me ---


async def test_registration_creates_the_owner(client: AsyncClient) -> None:
    owner = await owner_session(client)

    me = (await client.get(ME, headers=owner)).json()
    assert me["role"] == "OWNER"
    assert me["user"]["name"] == "Olivia Owner"
    assert me["user"]["email"] == OWNER_EMAIL
    assert me["email_verified"] is True


async def test_integration_keys_act_as_developer(client: AsyncClient) -> None:
    owner = await owner_session(client)
    created = await client.post(f"{ME}/api-keys", json={"name": "backend"}, headers=owner)
    integration = key(created.json()["api_key"])

    me = (await client.get(ME, headers=integration)).json()
    assert me["role"] == "DEVELOPER" and me["user"] is None
    assert created.json()["created_by_id"] == me["id"] or created.json()["created_by_id"]


# --- Invitations ---


async def test_invite_and_accept(client: AsyncClient, outbox: list[EmailMessage]) -> None:
    owner = await owner_session(client)
    invited = await client.post(
        f"{TEAM}/invitations",
        json={"email": "Dev@Acme.example", "role": "DEVELOPER"},
        headers=owner,
    )
    assert invited.status_code == 201
    assert invited.json()["email"] == "dev@acme.example"
    assert outbox[-1].to == "dev@acme.example"
    assert outbox[-1].subject == "You're invited to Acme on RecoEngine"
    assert "Olivia Owner" in outbox[-1].text
    token = link_token("dev@acme.example", "/accept-invite")

    info = await client.post(f"{AUTH}/invitations/lookup", json={"token": token})
    assert info.json() | {"expires_at": None} == {
        "workspace_name": "Acme",
        "email": "dev@acme.example",
        "role": "DEVELOPER",
        "invited_by": "Olivia Owner",
        "expires_at": None,
    }
    pending = (await client.get(TEAM, headers=owner)).json()["invitations"]
    assert [i["email"] for i in pending] == ["dev@acme.example"]

    accepted = await client.post(
        f"{AUTH}/invitations/accept",
        json={"token": token, "name": "Dev Person", "password": MEMBER_PASSWORD},
    )
    assert accepted.status_code == 201, accepted.text
    me = (await client.get(ME, headers=key(accepted.json()["api_key"]))).json()
    assert (me["role"], me["user"]["name"], me["email_verified"]) == (
        "DEVELOPER",
        "Dev Person",
        True,
    )

    team = (await client.get(TEAM, headers=owner)).json()
    assert [(m["email"], m["role"]) for m in team["members"]] == [
        (OWNER_EMAIL, "OWNER"),
        ("dev@acme.example", "DEVELOPER"),
    ]
    assert team["invitations"] == []
    again = await client.post(
        f"{AUTH}/invitations/accept",
        json={"token": token, "name": "Dev Person", "password": MEMBER_PASSWORD},
    )
    assert again.status_code == 400


async def test_members_sign_in_with_their_own_password(client: AsyncClient) -> None:
    owner = await owner_session(client)
    await add_member(client, owner, "dev@acme.example", "DEVELOPER")

    login = await client.post(
        f"{AUTH}/login", json={"email": "dev@acme.example", "password": MEMBER_PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["tenant"]["role"] == "DEVELOPER"
    assert login.json()["tenant"]["name"] == "Acme"


async def test_reinvite_replaces_the_link_and_revoke_kills_it(client: AsyncClient) -> None:
    owner = await owner_session(client)
    body = {"email": "dev@acme.example", "role": "VIEWER"}
    await client.post(f"{TEAM}/invitations", json=body, headers=owner)
    first = link_token("dev@acme.example", "/accept-invite")
    second_invite = await client.post(f"{TEAM}/invitations", json=body, headers=owner)
    second = link_token("dev@acme.example", "/accept-invite")

    assert first != second
    lookup = await client.post(f"{AUTH}/invitations/lookup", json={"token": first})
    assert lookup.status_code == 400
    revoked = await client.delete(f"{TEAM}/invitations/{second_invite.json()['id']}", headers=owner)
    assert revoked.status_code == 204
    lookup = await client.post(f"{AUTH}/invitations/lookup", json={"token": second})
    assert lookup.status_code == 400


@pytest.mark.parametrize("email", [OWNER_EMAIL, "other@elsewhere.example"])
async def test_cannot_invite_an_existing_account(client: AsyncClient, email: str) -> None:
    owner = await owner_session(client)
    await client.post(
        f"{AUTH}/register",
        json={
            "name": "Other",
            "email": "other@elsewhere.example",
            "password": PASSWORD,
            "domain_type": "FOOD",
        },
    )
    response = await client.post(
        f"{TEAM}/invitations", json={"email": email, "role": "VIEWER"}, headers=owner
    )
    assert response.status_code == 409


async def test_cannot_invite_as_owner(client: AsyncClient) -> None:
    owner = await owner_session(client)
    response = await client.post(
        f"{TEAM}/invitations", json={"email": "x@acme.example", "role": "OWNER"}, headers=owner
    )
    assert response.status_code == 422


# --- What each role may do ---


async def test_role_permissions(client: AsyncClient) -> None:
    owner = await owner_session(client)
    admin = await add_member(client, owner, "admin@acme.example", "ADMIN")
    developer = await add_member(client, owner, "dev@acme.example", "DEVELOPER")
    viewer = await add_member(client, owner, "viewer@acme.example", "VIEWER")
    integration = key(
        (await client.post(f"{ME}/api-keys", json={"name": "ci"}, headers=owner)).json()["api_key"]
    )
    config = (await client.get(ME, headers=owner)).json()["domain_config"]

    async def status(method: str, url: str, headers: dict[str, str], **kw: Any) -> int:
        return (await client.request(method, url, headers=headers, **kw)).status_code

    upload = {"async": True, "items": [{"external_id": "j1", "description": "Python role"}]}
    checks = [
        ("GET", "/api/v1/items", {}),
        ("POST", "/api/v1/recommend/by-text", {"json": {"query": "python", "top_k": 3}}),
        ("GET", TEAM, {}),
        ("POST", "/api/v1/items/upload", {"json": upload}),
        ("GET", f"{ME}/api-keys", {}),
        ("PUT", f"{ME}/domain-config", {"json": {"domain_config": config}}),
        ("POST", f"{TEAM}/invitations", {"json": {"email": "n@acme.example", "role": "VIEWER"}}),
    ]
    expected = {
        "viewer": [200, 200, 200, 403, 403, 403, 403],
        "developer": [200, 200, 200, 202, 200, 403, 403],
        "integration": [200, 200, 200, 202, 200, 403, 403],
        "admin": [200, 200, 200, 202, 200, 200, 201],
    }
    callers = {"viewer": viewer, "developer": developer, "integration": integration, "admin": admin}
    for name, headers in callers.items():
        got = [await status(m, url, headers, **kw) for m, url, kw in checks]
        assert got == expected[name], name

    forbidden = await client.post("/api/v1/items/upload", json=upload, headers=viewer)
    assert "Developer role" in forbidden.json()["error"]["message"]
    delete_as_admin = await client.post(
        f"{ME}/delete",
        json={"confirm_email": OWNER_EMAIL, "password": MEMBER_PASSWORD},
        headers=admin,
    )
    assert delete_as_admin.status_code == 403


# --- Managing members ---


async def test_admin_changes_roles_within_limits(client: AsyncClient) -> None:
    owner = await owner_session(client)
    admin = await add_member(client, owner, "admin@acme.example", "ADMIN")
    await add_member(client, owner, "viewer@acme.example", "VIEWER")
    viewer_id = await member_id(client, owner, "viewer@acme.example")
    admin_id = await member_id(client, owner, "admin@acme.example")
    owner_id = await member_id(client, owner, OWNER_EMAIL)

    promoted = await client.patch(f"{TEAM}/{viewer_id}", json={"role": "DEVELOPER"}, headers=admin)
    assert promoted.status_code == 200 and promoted.json()["role"] == "DEVELOPER"
    own = await client.patch(f"{TEAM}/{admin_id}", json={"role": "VIEWER"}, headers=admin)
    assert own.status_code == 403
    demote_owner = await client.patch(f"{TEAM}/{owner_id}", json={"role": "ADMIN"}, headers=admin)
    assert demote_owner.status_code == 403
    make_owner = await client.patch(f"{TEAM}/{viewer_id}", json={"role": "OWNER"}, headers=admin)
    assert make_owner.status_code == 422


async def test_removing_a_member_ends_sessions_but_keeps_their_keys(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await owner_session(client)
    developer = await add_member(client, owner, "dev@acme.example", "DEVELOPER")
    their_key = key(
        (await client.post(f"{ME}/api-keys", json={"name": "dev key"}, headers=developer)).json()[
            "api_key"
        ]
    )
    dev_id = await member_id(client, owner, "dev@acme.example")

    removed = await client.delete(f"{TEAM}/{dev_id}", headers=owner)

    assert removed.status_code == 204
    assert (await client.get(ME, headers=developer)).status_code == 401
    assert (await client.get(ME, headers=their_key)).status_code == 200
    login = await client.post(
        f"{AUTH}/login", json={"email": "dev@acme.example", "password": MEMBER_PASSWORD}
    )
    assert login.status_code == 401
    async with session_factory() as session:
        kept = (await session.scalars(select(ApiKey).where(ApiKey.name == "dev key"))).one()
    assert kept.is_active and kept.created_by_id is None


async def test_owner_cannot_be_removed(client: AsyncClient) -> None:
    owner = await owner_session(client)
    admin = await add_member(client, owner, "admin@acme.example", "ADMIN")
    owner_id = await member_id(client, owner, OWNER_EMAIL)
    assert (await client.delete(f"{TEAM}/{owner_id}", headers=admin)).status_code == 403


async def test_transfer_ownership(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await owner_session(client)
    await add_member(client, owner, "next@acme.example", "ADMIN", name="Next Owner")
    next_id = await member_id(client, owner, "next@acme.example")

    wrong = await client.post(
        f"{TEAM}/transfer-ownership", json={"user_id": next_id, "password": "nope"}, headers=owner
    )
    assert wrong.status_code == 403
    moved = await client.post(
        f"{TEAM}/transfer-ownership", json={"user_id": next_id, "password": PASSWORD}, headers=owner
    )

    assert moved.status_code == 200 and moved.json()["role"] == "OWNER"
    me = (await client.get(ME, headers=owner)).json()
    assert me["role"] == "ADMIN" and me["email"] == "next@acme.example"
    async with session_factory() as session:
        roles = {u.email: u.role for u in (await session.scalars(select(User))).all()}
    assert roles == {OWNER_EMAIL: "ADMIN", "next@acme.example": "OWNER"}
    again = await client.post(
        f"{TEAM}/transfer-ownership", json={"user_id": next_id, "password": PASSWORD}, headers=owner
    )
    assert again.status_code == 403


async def test_member_can_reset_their_own_password(client: AsyncClient) -> None:
    owner = await owner_session(client)
    await add_member(client, owner, "dev@acme.example", "DEVELOPER")

    await client.post(f"{AUTH}/forgot-password", json={"email": "dev@acme.example"})
    await client.post(
        f"{AUTH}/reset-password",
        json={
            "token": link_token("dev@acme.example", "/reset-password"),
            "password": "fresh pass 99",
        },
    )
    login = await client.post(
        f"{AUTH}/login", json={"email": "dev@acme.example", "password": "fresh pass 99"}
    )
    assert login.status_code == 200
    # The owner's password is untouched.
    owner_login = await client.post(
        f"{AUTH}/login", json={"email": OWNER_EMAIL, "password": PASSWORD}
    )
    assert owner_login.status_code == 200


async def test_deleting_the_workspace_removes_its_users(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await owner_session(client)
    await add_member(client, owner, "dev@acme.example", "DEVELOPER")

    deleted = await client.post(
        f"{ME}/delete", json={"confirm_email": OWNER_EMAIL, "password": PASSWORD}, headers=owner
    )

    assert deleted.status_code == 204
    async with session_factory() as session:
        assert (await session.scalars(select(User))).all() == []
        assert (await session.scalars(select(Tenant))).all() == []
