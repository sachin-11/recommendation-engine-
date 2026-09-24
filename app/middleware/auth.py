"""X-API-Key authentication for tenant-facing routes.

Implemented as a FastAPI dependency rather than ASGI middleware, so it shares the
request's DB session, shows up in the OpenAPI docs, and is attached per router
(`/api/v1/items/*`, `/api/v1/index/*`) instead of matching URL prefixes by hand.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

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

API_KEY_HEADER = "X-API-Key"
# Writing last_used_at on every request would turn each read into a write.
LAST_USED_RESOLUTION = timedelta(minutes=1)

api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthContext:
    tenant: Tenant
    api_key: ApiKey


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

    if api_key.last_used_at is None or now - _as_utc(api_key.last_used_at) >= LAST_USED_RESOLUTION:
        api_key.last_used_at = now
        await db.commit()

    request.state.tenant = tenant
    request.state.api_key = api_key
    return AuthContext(tenant=tenant, api_key=api_key)


AuthDep = Annotated[AuthContext, Depends(authenticate)]
