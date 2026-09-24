"""Dashboard accounts: registration with a password, login sessions, and self-service
management of the signed-in tenant (API keys, domain config, deletion).

A dashboard session is an ordinary API key flagged `is_session` with a 7-day expiry, so
every existing X-API-Key route works for the dashboard unchanged.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.passwords import hash_password, verify_password
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.base import utcnow
from app.models.tenant import Tenant
from app.schemas.account import RegisterRequest
from app.schemas.tenant import ApiKeyCreate, DomainConfig, TenantCreate
from app.services.embedding.pinecone_service import PineconeService, VectorStoreUnavailableError
from app.services.tenant_service import TenantService

SESSION_TTL = timedelta(days=7)
SESSION_KEY_NAME = "Dashboard session"
FIRST_KEY_NAME = "Default key"
# Changing any of these makes stored vectors or their metadata stale.
_REBUILD_FIELDS = ("primary_embedding_field", "searchable_fields", "filter_fields")


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenants = TenantService(session)

    async def register(self, data: RegisterRequest) -> tuple[Tenant, ApiKey, str]:
        """Create the tenant and its first API key. Returns (tenant, key, plain key)."""
        tenant_data = TenantCreate.model_validate(data.model_dump(exclude={"password"}))
        tenant = await self._tenants.create_tenant(tenant_data, hash_password(data.password))
        api_key, plain_key = await self._tenants.create_api_key(
            tenant.id, ApiKeyCreate(name=FIRST_KEY_NAME)
        )
        return tenant, api_key, plain_key

    async def login(self, email: str, password: str) -> tuple[Tenant, ApiKey, str]:
        tenant = await self._session.scalar(select(Tenant).where(Tenant.email == email))
        # verify_password runs even for unknown emails, so timing does not reveal accounts.
        if not verify_password(password, tenant.password_hash if tenant else None) or not tenant:
            raise UnauthorizedError("Invalid email or password")
        if not tenant.is_active:
            raise ForbiddenError("Tenant is inactive")

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
        return tenant, session_key, generated.plain_key

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
        """Delete the Pinecone index, then the tenant (items, keys and logs cascade)."""
        if confirm_email.lower() != tenant.email:
            raise BadRequestError("confirm_email does not match the account email")
        if tenant.has_password and not verify_password(password or "", tenant.password_hash):
            raise UnauthorizedError("Password is incorrect")
        try:
            await vector_store.delete_index(tenant.id)
        except VectorStoreUnavailableError as exc:
            raise ServiceUnavailableError(
                f"Could not delete the vector index, nothing was deleted: {exc}"
            ) from exc
        await self._session.delete(tenant)
        await self._session.commit()


def get_account_service(session: Annotated[AsyncSession, Depends(get_db)]) -> AccountService:
    return AccountService(session)


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
