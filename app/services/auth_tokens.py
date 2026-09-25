"""Single-use tokens for email links (verify email, reset password).

The plain token only ever exists in the email; the database keeps its keyed hash, the
purpose and an expiry. Issuing a new token invalidates the tenant's older unused ones for
the same purpose, so only the latest link works.
"""

import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.core.security import hash_api_key
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.base import utcnow
from app.models.tenant import Tenant

INVALID_LINK = "This link is invalid or has expired. Request a new one."


class AuthTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, tenant: Tenant, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
        """Create a token, retire older unused ones, commit, and return the plain token."""
        now = utcnow()
        await self._session.execute(
            delete(AuthToken).where(
                AuthToken.tenant_id == tenant.id,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
            )
        )
        plain = secrets.token_urlsafe(32)
        self._session.add(
            AuthToken(
                tenant_id=tenant.id,
                purpose=purpose,
                token_hash=hash_api_key(plain),
                expires_at=now + ttl,
            )
        )
        await self._session.commit()
        return plain

    async def consume(self, plain: str, purpose: AuthTokenPurpose) -> Tenant:
        """Mark the token used and return its tenant. The caller commits.

        The conditional UPDATE makes a token usable once even under concurrent requests.
        """
        now = utcnow()
        tenant_id = await self._session.scalar(
            update(AuthToken)
            .where(
                AuthToken.token_hash == hash_api_key(plain),
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthToken.tenant_id)
            .execution_options(synchronize_session=False)
        )
        tenant = await self._session.get(Tenant, tenant_id) if tenant_id else None
        if tenant is None or not tenant.is_active:
            await self._session.rollback()
            raise BadRequestError(INVALID_LINK)
        return tenant

    async def has_pending(self, tenant: Tenant, purpose: AuthTokenPurpose) -> bool:
        found = await self._session.scalar(
            select(AuthToken.id)
            .where(
                AuthToken.tenant_id == tenant.id,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > utcnow(),
            )
            .limit(1)
        )
        return found is not None
