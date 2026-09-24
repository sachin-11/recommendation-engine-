"""Version 1 API routes. Handlers stay thin: validate, delegate to the service, serialise."""

import uuid

from fastapi import APIRouter, status

from app.api.v1 import account, analytics, recommend
from app.api.v1.items import index_router, items_router
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.schemas.tenant import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    TenantCreate,
    TenantResponse,
)
from app.services.tenant_service import TenantServiceDep

api_router = APIRouter()

tenants_router = APIRouter(prefix="/tenants", tags=["tenants"], responses=ERROR_RESPONSES)


@tenants_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant",
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
async def create_tenant(payload: TenantCreate, service: TenantServiceDep) -> TenantResponse:
    tenant = await service.create_tenant(payload)
    return TenantResponse.model_validate(tenant)


@tenants_router.get("/{tenant_id}", summary="Get tenant details")
async def get_tenant(tenant_id: uuid.UUID, service: TenantServiceDep) -> TenantResponse:
    tenant = await service.get_tenant(tenant_id)
    return TenantResponse.model_validate(tenant)


@tenants_router.post(
    "/{tenant_id}/api-keys",
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new API key (the plain key is returned only once)",
    responses={403: {"model": ErrorResponse, "description": "Tenant is inactive"}},
)
async def create_api_key(
    tenant_id: uuid.UUID, payload: ApiKeyCreate, service: TenantServiceDep
) -> ApiKeyCreatedResponse:
    api_key, plain_key = await service.create_api_key(tenant_id, payload)
    return ApiKeyCreatedResponse.model_validate(
        {**ApiKeyResponse.model_validate(api_key).model_dump(), "api_key": plain_key}
    )


@tenants_router.get("/{tenant_id}/api-keys", summary="List API keys (prefix only)")
async def list_api_keys(tenant_id: uuid.UUID, service: TenantServiceDep) -> list[ApiKeyResponse]:
    api_keys = await service.list_api_keys(tenant_id)
    return [ApiKeyResponse.model_validate(key) for key in api_keys]


@tenants_router.delete(
    "/{tenant_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(
    tenant_id: uuid.UUID, key_id: uuid.UUID, service: TenantServiceDep
) -> None:
    await service.revoke_api_key(tenant_id, key_id)


api_router.include_router(tenants_router)
api_router.include_router(items_router)
api_router.include_router(index_router)
api_router.include_router(recommend.router)
api_router.include_router(analytics.router)
api_router.include_router(account.auth_router)
api_router.include_router(account.me_router)
