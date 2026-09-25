"""Email verification, password reset and sign-in lockout (Module 6)."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import email as email_module
from app.core.config import Settings, settings
from app.core.email import (
    EmailMessage,
    MemoryEmailSender,
    SmtpEmailSender,
    mask_address,
    ses_sender,
    ses_smtp_password,
)
from app.models import AuthToken
from tests.conftest import ADMIN_HEADERS, link_token

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
EMAIL = "owner@acme.example"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new passphrase"


async def register(client: AsyncClient, email: str = EMAIL) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH}/register",
        json={"name": "Acme", "email": email, "password": PASSWORD, "domain_type": "HR"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def key(value: str) -> dict[str, str]:
    return {"X-API-Key": value}


async def login(client: AsyncClient, password: str = PASSWORD) -> Any:
    return await client.post(f"{AUTH}/login", json={"email": EMAIL, "password": password})


# --- Verification ---


async def test_register_emails_a_single_use_verification_link(
    client: AsyncClient,
    outbox: list[EmailMessage],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await register(client)

    assert [m.to for m in outbox] == [EMAIL]
    assert outbox[0].subject == "Confirm your email for RecoEngine"
    token = link_token(EMAIL, "/verify-email")
    assert token in outbox[0].html
    async with session_factory() as session:
        stored = (await session.scalars(select(AuthToken))).one()
    assert stored.token_hash != token and token not in stored.token_hash

    verified = await client.post(f"{AUTH}/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True
    me = await client.get(ME, headers=key(body["api_key"]))
    assert me.json()["email_verified"] is True

    again = await client.post(f"{AUTH}/verify-email", json={"token": token})
    assert again.status_code == 400
    assert "invalid or has expired" in again.json()["error"]["message"]


async def test_expired_verification_link_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await register(client)
    async with session_factory() as session:
        await session.execute(
            update(AuthToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    response = await client.post(
        f"{AUTH}/verify-email", json={"token": link_token(EMAIL, "/verify-email")}
    )
    assert response.status_code == 400


async def test_unknown_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/verify-email", json={"token": "x" * 43})
    assert response.status_code == 400


async def test_api_keys_need_a_verified_email(client: AsyncClient) -> None:
    session = key((await register(client))["api_key"])

    blocked = await client.post(f"{ME}/api-keys", json={"name": "backend"}, headers=session)
    assert blocked.status_code == 403
    assert "Verify your email" in blocked.json()["error"]["message"]

    await client.post(f"{AUTH}/verify-email", json={"token": link_token(EMAIL, "/verify-email")})
    allowed = await client.post(f"{ME}/api-keys", json={"name": "backend"}, headers=session)
    assert allowed.status_code == 201


async def test_verification_can_be_turned_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    body = await register(client)

    assert body["verification_required"] is False
    created = await client.post(
        f"{ME}/api-keys", json={"name": "backend"}, headers=key(body["api_key"])
    )
    assert created.status_code == 201


async def test_resend_replaces_the_previous_link(
    client: AsyncClient, outbox: list[EmailMessage]
) -> None:
    session = key((await register(client))["api_key"])
    first = link_token(EMAIL, "/verify-email")

    resent = await client.post(f"{AUTH}/resend-verification", headers=session)
    assert resent.status_code == 202
    second = link_token(EMAIL, "/verify-email")
    assert second != first and len(outbox) == 2

    assert (await client.post(f"{AUTH}/verify-email", json={"token": first})).status_code == 400
    assert (await client.post(f"{AUTH}/verify-email", json={"token": second})).status_code == 200


async def test_resend_is_limited_and_refused_once_verified(client: AsyncClient) -> None:
    session = key((await register(client))["api_key"])

    assert (await client.post(f"{AUTH}/resend-verification", headers=session)).status_code == 202
    assert (await client.post(f"{AUTH}/resend-verification", headers=session)).status_code == 429


async def test_resend_after_verification_is_a_conflict(client: AsyncClient) -> None:
    session = key((await register(client))["api_key"])
    await client.post(f"{AUTH}/verify-email", json={"token": link_token(EMAIL, "/verify-email")})

    response = await client.post(f"{AUTH}/resend-verification", headers=session)
    assert response.status_code == 409


async def test_unverified_accounts_get_a_small_upload_allowance(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UNVERIFIED_DAILY_ITEM_LIMIT", 2)
    session = key((await register(client))["api_key"])
    items = [{"external_id": f"j{i}", "description": "Python role"} for i in range(3)]

    blocked = await client.post(
        "/api/v1/items/upload", json={"async": True, "items": items}, headers=session
    )
    assert blocked.status_code == 429
    assert "limit of 2 items" in blocked.json()["error"]["message"]

    await client.post(f"{AUTH}/verify-email", json={"token": link_token(EMAIL, "/verify-email")})
    allowed = await client.post(
        "/api/v1/items/upload", json={"async": True, "items": items}, headers=session
    )
    assert allowed.status_code == 202


async def test_operator_created_tenants_are_verified(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/tenants",
        json={"name": "Api", "email": "api@acme.example", "domain_type": "HR"},
        headers=ADMIN_HEADERS,
    )
    tenant_id = created.json()["id"]
    issued = await client.post(
        f"/api/v1/tenants/{tenant_id}/api-keys", json={"name": "ops"}, headers=ADMIN_HEADERS
    )
    me = await client.get(ME, headers=key(issued.json()["api_key"]))
    assert me.json()["email_verified"] is True


# --- Password reset ---


async def test_forgot_password_does_not_reveal_accounts(
    client: AsyncClient, outbox: list[EmailMessage]
) -> None:
    await register(client)
    outbox.clear()

    unknown = await client.post(f"{AUTH}/forgot-password", json={"email": "nobody@acme.example"})
    known = await client.post(f"{AUTH}/forgot-password", json={"email": "OWNER@acme.example"})

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [m.to for m in outbox] == [EMAIL]
    assert outbox[0].subject == "Reset your RecoEngine password"


async def test_reset_password_signs_out_sessions_but_keeps_integration_keys(
    client: AsyncClient, outbox: list[EmailMessage]
) -> None:
    body = await register(client)
    await client.post(f"{AUTH}/verify-email", json={"token": link_token(EMAIL, "/verify-email")})
    integration = (
        await client.post(f"{ME}/api-keys", json={"name": "backend"}, headers=key(body["api_key"]))
    ).json()["api_key"]
    await client.post(f"{AUTH}/forgot-password", json={"email": EMAIL})
    token = link_token(EMAIL, "/reset-password")

    reset = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )

    assert reset.status_code == 200, reset.text
    assert (await client.get(ME, headers=key(body["api_key"]))).status_code == 401
    assert (await client.get(ME, headers=key(integration))).status_code == 200
    assert (await login(client)).status_code == 401
    assert (await login(client, NEW_PASSWORD)).status_code == 200
    assert outbox[-1].subject == "Your RecoEngine password was changed"
    reused = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "password": "another one here"}
    )
    assert reused.status_code == 400


async def test_reset_link_also_verifies_the_email(client: AsyncClient) -> None:
    await register(client)
    await client.post(f"{AUTH}/forgot-password", json={"email": EMAIL})
    await client.post(
        f"{AUTH}/reset-password",
        json={"token": link_token(EMAIL, "/reset-password"), "password": NEW_PASSWORD},
    )

    session = (await login(client, NEW_PASSWORD)).json()
    assert session["tenant"]["email_verified"] is True


async def test_tokens_only_work_for_their_purpose(client: AsyncClient) -> None:
    await register(client)
    response = await client.post(
        f"{AUTH}/reset-password",
        json={"token": link_token(EMAIL, "/verify-email"), "password": NEW_PASSWORD},
    )
    assert response.status_code == 400


async def test_reset_password_validates_the_new_password(client: AsyncClient) -> None:
    await register(client)
    await client.post(f"{AUTH}/forgot-password", json={"email": EMAIL})
    response = await client.post(
        f"{AUTH}/reset-password",
        json={"token": link_token(EMAIL, "/reset-password"), "password": "short"},
    )
    assert response.status_code == 422


async def test_tenant_without_password_can_set_one_by_reset(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/tenants",
        json={"name": "Api", "email": EMAIL, "domain_type": "HR"},
        headers=ADMIN_HEADERS,
    )
    await client.post(f"{AUTH}/forgot-password", json={"email": EMAIL})
    await client.post(
        f"{AUTH}/reset-password",
        json={"token": link_token(EMAIL, "/reset-password"), "password": NEW_PASSWORD},
    )
    assert (await login(client, NEW_PASSWORD)).status_code == 200


# --- Sign-in lockout ---


async def test_wrong_passwords_lock_the_email(client: AsyncClient) -> None:
    await register(client)
    codes = [(await login(client, "wrong password")).status_code for _ in range(5)]

    assert codes == [401, 401, 401, 401, 429]
    locked = await login(client)
    assert locked.status_code == 429
    assert "reset your password" in locked.json()["error"]["message"]
    assert 0 < int(locked.headers["Retry-After"]) <= 15 * 60


async def test_successful_sign_in_clears_failures(client: AsyncClient) -> None:
    await register(client)
    for _ in range(4):
        await login(client, "wrong password")
    assert (await login(client)).status_code == 200
    codes = [(await login(client, "wrong password")).status_code for _ in range(4)]
    assert codes == [401] * 4


async def test_password_reset_unlocks_the_email(client: AsyncClient) -> None:
    await register(client)
    for _ in range(5):
        await login(client, "wrong password")
    await client.post(f"{AUTH}/forgot-password", json={"email": EMAIL})
    await client.post(
        f"{AUTH}/reset-password",
        json={"token": link_token(EMAIL, "/reset-password"), "password": NEW_PASSWORD},
    )
    assert (await login(client, NEW_PASSWORD)).status_code == 200


async def test_registrations_are_limited_per_client(client: AsyncClient) -> None:
    codes = []
    for i in range(6):
        response = await client.post(
            f"{AUTH}/register",
            json={
                "name": "Acme",
                "email": f"owner{i}@acme.example",
                "password": PASSWORD,
                "domain_type": "HR",
            },
        )
        codes.append(response.status_code)
    assert codes == [201] * 5 + [429]


# --- Delivery ---


async def test_email_failures_do_not_break_sign_up(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: MemoryEmailSender, message: EmailMessage) -> None:
        raise ConnectionError("smtp down")

    monkeypatch.setattr(MemoryEmailSender, "send", broken)
    body = await register(client)
    assert body["verification_required"] is True


class FakeSmtp:
    instances: list["FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float, **_: Any) -> None:
        self.calls: list[tuple[str, Any]] = [("connect", (host, port))]
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append(("quit", None))

    def starttls(self, context: Any) -> None:
        self.calls.append(("starttls", None))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", (username, password)))

    def send_message(self, message: Any) -> None:
        self.calls.append(("send", message))


async def test_smtp_sender_uses_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "apikey")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", SecretStr("s3cret"))
    monkeypatch.setattr(settings, "SMTP_SECURITY", "starttls")
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSmtp)
    FakeSmtp.instances.clear()

    await SmtpEmailSender().send(
        EmailMessage(to="a@b.example", subject="Hi", text="plain", html="<p>html</p>")
    )

    calls = FakeSmtp.instances[0].calls
    assert [name for name, _ in calls] == ["connect", "starttls", "login", "send", "quit"]
    assert calls[0][1] == ("smtp.example.com", 587)
    assert calls[2][1] == ("apikey", "s3cret")
    sent = calls[3][1]
    assert sent["To"] == "a@b.example" and sent["Subject"] == "Hi"
    assert sent.get_body(("html",)).get_content().strip() == "<p>html</p>"


def test_ses_smtp_password_shape() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    password = ses_smtp_password(secret, "us-east-1")
    raw = base64.b64decode(password)
    # Version byte 0x04 followed by an HMAC-SHA256 signature; region-specific.
    assert raw[0] == 0x04 and len(raw) == 33
    assert password == ses_smtp_password(secret, "us-east-1")
    assert password != ses_smtp_password(secret, "eu-west-1")


async def test_ses_backend_sends_through_the_regional_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "AWS_REGION", "ap-south-1")
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", SecretStr("secret"))
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSmtp)
    FakeSmtp.instances.clear()

    await ses_sender().send(EmailMessage(to="a@b.example", subject="Hi", text="t", html="<p>h</p>"))

    calls = FakeSmtp.instances[0].calls
    assert calls[0][1] == ("email-smtp.ap-south-1.amazonaws.com", 587)
    assert calls[1][0] == "starttls"
    assert calls[2][1] == ("AKIDEXAMPLE", ses_smtp_password("secret", "ap-south-1"))


def test_mask_address() -> None:
    assert mask_address("sachin@example.com") == "s***@example.com"
    assert mask_address("nope") == "***"


def test_production_requires_smtp() -> None:
    base: dict[str, Any] = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://u:p@db/reco",
        "REDIS_URL": "redis://redis",
        "SECRET_KEY": "k" * 40,
        "OPENAI_API_KEY": "sk-test",
        "PINECONE_API_KEY": "pc-test",
    }
    with pytest.raises(ValueError, match="EMAIL_BACKEND must be 'smtp'"):
        Settings(**base, EMAIL_BACKEND="console")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="SMTP_HOST is required"):
        Settings(**base, EMAIL_BACKEND="smtp", SMTP_HOST="")  # type: ignore[arg-type]
    assert Settings(**base, EMAIL_BACKEND="smtp", SMTP_HOST="smtp.example.com")  # type: ignore[arg-type]
