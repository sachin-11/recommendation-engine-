"""X-API-Key authentication and role checks for tenant-facing routes.

A dashboard session key belongs to a user and carries that user's role. An integration
key belongs to the workspace and acts with INTEGRATION_KEY_ROLE.

Implemented as a FastAPI dependency rather than ASGI middleware, so it shares the
request's DB session, shows up in the OpenAPI docs, and is attached per router
(`/api/v1/items/*`, `/api/v1/index/*`) instead of matching URL prefixes by hand.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import hash_api_key
from app.models.api_key import ApiKey
from app.models.base import utcnow
from app.models.tenant import Tenant
from app.models.user import Role, User

API_KEY_HEADER = "X-API-Key"
# Writing last_used_at on every request would turn each read into a write.
LAST_USED_RESOLUTION = timedelta(minutes=1)
# What an integration (non-session) API key may do: read, recommend, and manage items/keys.
INTEGRATION_KEY_ROLE = Role.DEVELOPER

api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER,
    scheme_name="ApiKeyAuth",
    description="Your API key, e.g. `reco_…`. Create keys in the dashboard or via /me/api-keys.",
    auto_error=False,
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    tenant: Tenant
    api_key: ApiKey
    # The signed-in person for a dashboard session; None for an integration key.
    user: User | None
    role: Role

    @property
    def email_verified(self) -> bool:
        """The acting user's email, or the workspace owner's for an integration key."""
        return self.user.email_verified if self.user else self.tenant.email_verified


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def authenticate(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    raw_key: Annotated[str | None, Security(api_key_scheme)],
) -> AuthContext:
    """Resolve the X-API-Key header to its tenant and put both on `request.state`."""
    if not raw_key:
        raise UnauthorizedError(
            f"Missing {API_KEY_HEADER} header", headers={"WWW-Authenticate": API_KEY_HEADER}
        )

    row = (
        await db.execute(
            select(ApiKey, Tenant)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.key_hash == hash_api_key(raw_key))
        )
    ).one_or_none()
    if row is None or not row.ApiKey.is_active:
        raise UnauthorizedError("Invalid API key", headers={"WWW-Authenticate": API_KEY_HEADER})
    api_key, tenant = row.ApiKey, row.Tenant

    now = utcnow()
    if api_key.expires_at is not None and _as_utc(api_key.expires_at) <= now:
        raise UnauthorizedError("API key has expired", headers={"WWW-Authenticate": API_KEY_HEADER})
    if not tenant.is_active:
        raise ForbiddenError("Tenant is inactive")

    user: User | None = None
    role = INTEGRATION_KEY_ROLE
    if api_key.user_id is not None:
        user = await db.get(User, api_key.user_id)
        if user is None or not user.is_active or user.tenant_id != tenant.id:
            raise UnauthorizedError(
                "This session has ended", headers={"WWW-Authenticate": API_KEY_HEADER}
            )
        role = user.role

    if api_key.last_used_at is None or now - _as_utc(api_key.last_used_at) >= LAST_USED_RESOLUTION:
        api_key.last_used_at = now
        await db.commit()

    request.state.tenant = tenant
    request.state.api_key = api_key
    return AuthContext(tenant=tenant, api_key=api_key, user=user, role=role)


AuthDep = Annotated[AuthContext, Depends(authenticate)]


def require_role(minimum: Role) -> Any:
    """A route dependency that lets through `minimum` and higher roles."""

    async def check(auth: AuthDep) -> AuthContext:
        if not auth.role.at_least(minimum):
            who = (
                "API keys act as Developer" if auth.user is None else f"you are {auth.role.title()}"
            )
            raise ForbiddenError(f"This needs the {minimum.title()} role or higher ({who}).")
        return auth

    return Depends(check)
