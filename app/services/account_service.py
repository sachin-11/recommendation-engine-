"""Dashboard accounts: registration with a password, email verification, login sessions,
password reset, and self-service management of the signed-in tenant (API keys, domain
config, deletion).

A dashboard session is an ordinary API key flagged `is_session` with a 7-day expiry, so
every existing X-API-Key route works for the dashboard unchanged.
"""

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
from app.schemas.account import RegisterRequest
from app.schemas.tenant import DomainConfig, TenantCreate
from app.services.auth_tokens import AuthTokenService
from app.services.embedding.pinecone_service import PineconeService, VectorStoreUnavailableError
from app.services.tenant_service import TenantService

SESSION_TTL = timedelta(days=7)
SESSION_KEY_NAME = "Dashboard session"
# Changing any of these makes stored vectors or their metadata stale.
_REBUILD_FIELDS = ("primary_embedding_field", "searchable_fields", "filter_fields")


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenants = TenantService(session)

    async def register(self, data: RegisterRequest) -> tuple[Tenant, ApiKey, str, str]:
        """Create the tenant and sign it in.

        Returns (tenant, session key, plain session key, email verification token). API keys
        for integrations are created from the dashboard once the email is verified.
        """
        tenant_data = TenantCreate.model_validate(data.model_dump(exclude={"password"}))
        tenant = await self._tenants.create_tenant(tenant_data, hash_password(data.password))
        session_key, plain_key = await self._create_session(tenant)
        token = await self.issue_verification(tenant)
        return tenant, session_key, plain_key, token

    async def login(self, email: str, password: str) -> tuple[Tenant, ApiKey, str]:
        tenant = await self._session.scalar(select(Tenant).where(Tenant.email == email))
        # verify_password runs even for unknown emails, so timing does not reveal accounts.
        if not verify_password(password, tenant.password_hash if tenant else None) or not tenant:
            raise UnauthorizedError("Invalid email or password")
        if not tenant.is_active:
            raise ForbiddenError("Tenant is inactive")
        session_key, plain_key = await self._create_session(tenant)
        return tenant, session_key, plain_key

    async def _create_session(self, tenant: Tenant) -> tuple[ApiKey, str]:
        generated = generate_api_key()
        session_key = ApiKey(
            tenant_id=tenant.id,
            name=SESSION_KEY_NAME,
            key_hash=generated.key_hash,
            key_prefix=generated.key_prefix,
            expires_at=utcnow() + SESSION_TTL,
            is_session=True,
        )
        self._session.add(session_key)
        await self._session.commit()
        return session_key, generated.plain_key

    # --- Email verification ---

    async def issue_verification(self, tenant: Tenant) -> str:
        ttl = timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS)
        return await AuthTokenService(self._session).issue(
            tenant, AuthTokenPurpose.VERIFY_EMAIL, ttl
        )

    async def resend_verification(self, tenant: Tenant) -> str:
        if tenant.email_verified:
            raise ConflictError("This email address is already verified")
        return await self.issue_verification(tenant)

    async def verify_email(self, token: str) -> Tenant:
        tenant = await AuthTokenService(self._session).consume(token, AuthTokenPurpose.VERIFY_EMAIL)
        if tenant.email_verified_at is None:
            tenant.email_verified_at = utcnow()
        await self._session.commit()
        return tenant

    # --- Password reset ---

    async def request_password_reset(self, email: str) -> tuple[Tenant, str] | None:
        """A reset token for an active account, or None. Callers answer the same either way."""
        tenant = await self._session.scalar(select(Tenant).where(Tenant.email == email))
        if tenant is None or not tenant.is_active:
            return None
        ttl = timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
        token = await AuthTokenService(self._session).issue(
            tenant, AuthTokenPurpose.RESET_PASSWORD, ttl
        )
        return tenant, token

    async def reset_password(self, token: str, password: str) -> Tenant:
        """Set the password and sign out every dashboard session. Integration keys stay."""
        tenant = await AuthTokenService(self._session).consume(
            token, AuthTokenPurpose.RESET_PASSWORD
        )
        tenant.password_hash = hash_password(password)
        # The link reached the inbox, which proves the address as well.
        if tenant.email_verified_at is None:
            tenant.email_verified_at = utcnow()
        await self._session.execute(
            update(ApiKey)
            .where(ApiKey.tenant_id == tenant.id, ApiKey.is_session.is_(True))
            .values(is_active=False)
        )
        await self._session.commit()
        return tenant

    async def logout(self, api_key: ApiKey) -> None:
        """Revoke the key if it is a dashboard session; integration keys are left alone."""
        if api_key.is_session and api_key.is_active:
            api_key.is_active = False
            await self._session.commit()

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
        confirm_email: str,
        password: str | None,
        vector_store: PineconeService,
    ) -> None:
        """Delete the tenant's vectors, then the tenant (items, keys and logs cascade)."""
        if confirm_email.lower() != tenant.email:
            raise BadRequestError("confirm_email does not match the account email")
        if tenant.has_password and not verify_password(password or "", tenant.password_hash):
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
