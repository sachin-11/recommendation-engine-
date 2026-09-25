"""Dashboard accounts: registration, email verification, login sessions, password reset,
and self-service management of the signed-in workspace (API keys, domain config, deletion).

People sign in as users; a workspace (tenant) has one OWNER and any number of other members.
A dashboard session is an ordinary API key flagged `is_session`, tied to its user and
expiring after 7 days, so every X-API-Key route works for the dashboard unchanged.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.passwords import hash_password, verify_password
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.auth_token import AuthTokenPurpose
from app.models.base import utcnow
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.schemas.account import RegisterRequest
from app.schemas.tenant import DomainConfig, TenantCreate
from app.services.auth_tokens import AuthTokenService
from app.services.embedding.pinecone_service import PineconeService, VectorStoreUnavailableError
from app.services.tenant_service import TenantService

SESSION_TTL = timedelta(days=7)
SESSION_KEY_NAME = "Dashboard session"
# Changing any of these makes stored vectors or their metadata stale.
_REBUILD_FIELDS = ("primary_embedding_field", "searchable_fields", "filter_fields")


@dataclass(frozen=True, slots=True)
class Session:
    tenant: Tenant
    user: User
    key: ApiKey
    plain_key: str


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenants = TenantService(session)

    async def register(self, data: RegisterRequest) -> tuple[Session, str]:
        """Create the workspace and its owner, and sign the owner in.

        Returns the session and an email verification token. Integration API keys are
        created from the dashboard once the email is verified.
        """
        tenant_data = TenantCreate.model_validate(
            data.model_dump(exclude={"password", "owner_name"})
        )
        tenant, owner = await self._tenants.create_tenant(
            tenant_data, hash_password(data.password), owner_name=data.owner_name
        )
        session = await self.start_session(tenant, owner)
        return session, await self.issue_verification(owner)

    async def login(self, email: str, password: str) -> Session:
        user = await self._session.scalar(select(User).where(User.email == email))
        # verify_password runs even for unknown emails, so timing does not reveal accounts.
        if not verify_password(password, user.password_hash if user else None) or not user:
            raise UnauthorizedError("Invalid email or password")
        tenant = await self._session.get(Tenant, user.tenant_id)
        if tenant is None or not tenant.is_active:
            raise ForbiddenError("Tenant is inactive")
        if not user.is_active:
            raise ForbiddenError("This user has been deactivated")
        return await self.start_session(tenant, user)

    async def start_session(self, tenant: Tenant, user: User) -> Session:
        generated = generate_api_key()
        key = ApiKey(
            tenant_id=tenant.id,
            user_id=user.id,
            name=SESSION_KEY_NAME,
            key_hash=generated.key_hash,
            key_prefix=generated.key_prefix,
            expires_at=utcnow() + SESSION_TTL,
            is_session=True,
        )
        self._session.add(key)
        user.last_login_at = utcnow()
        await self._session.commit()
        return Session(tenant=tenant, user=user, key=key, plain_key=generated.plain_key)

    async def logout(self, api_key: ApiKey) -> None:
        """Revoke the key if it is a dashboard session; integration keys are left alone."""
        if api_key.is_session and api_key.is_active:
            api_key.is_active = False
            await self._session.commit()

    # --- Email verification ---

    async def issue_verification(self, user: User) -> str:
        ttl = timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS)
        return await AuthTokenService(self._session).issue(user, AuthTokenPurpose.VERIFY_EMAIL, ttl)

    async def resend_verification(self, user: User | None) -> tuple[User, str]:
        if user is None:
            raise BadRequestError("Sign in to the dashboard to verify your email")
        if user.email_verified:
            raise ConflictError("This email address is already verified")
        return user, await self.issue_verification(user)

    async def verify_email(self, token: str) -> tuple[Tenant, User]:
        user = await AuthTokenService(self._session).consume(token, AuthTokenPurpose.VERIFY_EMAIL)
        tenant = await self._mark_verified(user)
        await self._session.commit()
        return tenant, user

    async def _mark_verified(self, user: User) -> Tenant:
        """Verify the user; the owner's address is also the workspace's."""
        now = utcnow()
        if user.email_verified_at is None:
            user.email_verified_at = now
        tenant = await self._tenants.get_tenant(user.tenant_id)
        if user.role == Role.OWNER and tenant.email_verified_at is None:
            tenant.email_verified_at = now
        return tenant

    # --- Password reset ---

    async def request_password_reset(self, email: str) -> tuple[User, str] | None:
        """A reset token for an active user, or None. Callers answer the same either way."""
        user = await self._session.scalar(select(User).where(User.email == email))
        if user is None or not user.is_active:
            return None
        ttl = timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
        token = await AuthTokenService(self._session).issue(
            user, AuthTokenPurpose.RESET_PASSWORD, ttl
        )
        return user, token

    async def reset_password(self, token: str, password: str) -> User:
        """Set the password and sign out the user's dashboard sessions. API keys stay."""
        user = await AuthTokenService(self._session).consume(token, AuthTokenPurpose.RESET_PASSWORD)
        user.password_hash = hash_password(password)
        # The link reached the inbox, which proves the address as well.
        await self._mark_verified(user)
        await self._session.execute(
            update(ApiKey).where(ApiKey.user_id == user.id).values(is_active=False)
        )
        await self._session.commit()
        return user

    # --- Workspace settings ---

    async def list_api_keys(self, tenant: Tenant) -> list[ApiKey]:
        return [k for k in await self._tenants.list_api_keys(tenant.id) if not k.is_session]

    async def update_domain_config(self, tenant: Tenant, config: DomainConfig) -> bool:
        """Save the config. Returns True when existing vectors should be rebuilt."""
        new = config.model_dump(mode="json")
        rebuild = any(tenant.domain_config.get(f) != new[f] for f in _REBUILD_FIELDS)
        tenant.domain_config = new
        await self._session.commit()
        return rebuild

    async def delete_account(
        self,
        tenant: Tenant,
        user: User | None,
        confirm_email: str,
        password: str | None,
        vector_store: PineconeService,
    ) -> None:
        """Delete the workspace's vectors, then the workspace (users, items, keys and logs
        cascade). Only the owner, confirming the workspace email and their password."""
        if confirm_email.lower() != tenant.email:
            raise BadRequestError("confirm_email does not match the account email")
        if (
            user is not None
            and user.has_password
            and not verify_password(password or "", user.password_hash)
        ):
            raise UnauthorizedError("Password is incorrect")
        try:
            await vector_store.delete_tenant_vectors(tenant.id)
        except VectorStoreUnavailableError as exc:
            raise ServiceUnavailableError(
                f"Could not delete the tenant's vectors, nothing was deleted: {exc}"
            ) from exc
        await self._session.delete(tenant)
        await self._session.commit()


def get_account_service(session: Annotated[AsyncSession, Depends(get_db)]) -> AccountService:
    return AccountService(session)


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
