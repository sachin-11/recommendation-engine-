"""Single-use tokens for email links (verify email, reset password).

The plain token only ever exists in the email; the database keeps its keyed hash, the
purpose and an expiry. Issuing a new token invalidates the tenant's older unused ones for
the same purpose, so only the latest link works.
"""

import secrets
from datetime import timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.core.security import hash_api_key
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.base import utcnow
from app.models.tenant import Tenant
from app.models.user import User

INVALID_LINK = "This link is invalid or has expired. Request a new one."


class AuthTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, user: User, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
        """Create a token, retire older unused ones, commit, and return the plain token."""
        now = utcnow()
        await self._session.execute(
            delete(AuthToken).where(
                AuthToken.user_id == user.id,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
            )
        )
        plain = secrets.token_urlsafe(32)
        self._session.add(
            AuthToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_api_key(plain),
                expires_at=now + ttl,
            )
        )
        await self._session.commit()
        return plain

    async def consume(self, plain: str, purpose: AuthTokenPurpose) -> User:
        """Mark the token used and return its user. The caller commits.

        The conditional UPDATE makes a token usable once even under concurrent requests.
        """
        now = utcnow()
        user_id = await self._session.scalar(
            update(AuthToken)
            .where(
                AuthToken.token_hash == hash_api_key(plain),
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthToken.user_id)
            .execution_options(synchronize_session=False)
        )
        user = await self._session.get(User, user_id) if user_id else None
        tenant = await self._session.get(Tenant, user.tenant_id) if user else None
        if user is None or tenant is None or not (user.is_active and tenant.is_active):
            await self._session.rollback()
            raise BadRequestError(INVALID_LINK)
        return user
