"""Tenant (workspace) and API-key business logic."""

import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.base import utcnow
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.schemas.tenant import ApiKeyCreate, TenantCreate


class TenantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Tenants ---

    async def create_tenant(
        self,
        data: TenantCreate,
        password_hash: str | None = None,
        *,
        verified: bool = False,
        owner_name: str | None = None,
    ) -> tuple[Tenant, User]:
        """Create the workspace and its OWNER user (same email) in one transaction."""
        if await self._email_exists(data.email):
            raise ConflictError(f"An account with email '{data.email}' already exists")

        verified_at = utcnow() if verified else None
        tenant = Tenant(
            name=data.name,
            email=data.email,
            domain_type=data.domain_type,
            domain_config=data.resolved_domain_config().model_dump(mode="json"),
            email_verified_at=verified_at,
        )
        self._session.add(tenant)
        await self._session.flush()
        owner = User(
            tenant_id=tenant.id,
            email=data.email,
            name=owner_name or data.name,
            role=Role.OWNER,
            password_hash=password_hash,
            email_verified_at=verified_at,
        )
        self._session.add(owner)
        try:
            await self._session.commit()
        except IntegrityError as exc:  # concurrent registration with the same email
            await self._session.rollback()
            raise ConflictError(f"An account with email '{data.email}' already exists") from exc
        return tenant, owner

    async def get_tenant(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError(f"Tenant '{tenant_id}' not found")
        return tenant

    async def _email_exists(self, email: str) -> bool:
        """Emails are unique across workspaces and users (a user belongs to one workspace)."""
        for column in (Tenant.email, User.email):
            if (await self._session.execute(select(exists().where(column == email)))).scalar():
                return True
        return False

    # --- API keys ---

    async def create_api_key(
        self, tenant_id: uuid.UUID, data: ApiKeyCreate, created_by: User | None = None
    ) -> tuple[ApiKey, str]:
        """Create a key and return it together with the plain key (the only time it exists)."""
        tenant = await self.get_tenant(tenant_id)
        if not tenant.is_active:
            raise ForbiddenError("Cannot create API keys for an inactive tenant")

        generated = generate_api_key()
        api_key = ApiKey(
            tenant_id=tenant.id,
            name=data.name,
            key_hash=generated.key_hash,
            key_prefix=generated.key_prefix,
            expires_at=data.expires_at,
            created_by_id=created_by.id if created_by else None,
        )
        self._session.add(api_key)
        await self._session.commit()
        return api_key, generated.plain_key

    async def list_api_keys(self, tenant_id: uuid.UUID) -> list[ApiKey]:
        await self.get_tenant(tenant_id)
        result = await self._session.execute(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
        )
        return list(result.scalars().all())

    async def revoke_api_key(self, tenant_id: uuid.UUID, key_id: uuid.UUID) -> None:
        """Soft-revoke (kept for audit). Idempotent for already-revoked keys."""
        await self.get_tenant(tenant_id)
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            raise NotFoundError(f"API key '{key_id}' not found")
        if api_key.is_active:
            api_key.is_active = False
            await self._session.commit()


def get_tenant_service(session: Annotated[AsyncSession, Depends(get_db)]) -> TenantService:
    return TenantService(session)


TenantServiceDep = Annotated[TenantService, Depends(get_tenant_service)]
