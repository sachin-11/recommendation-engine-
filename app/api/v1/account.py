"""Dashboard sign-up/sign-in (`/auth`) and self-service for the signed-in tenant (`/me`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.database import get_db
from app.middleware.auth import AuthDep
from app.middleware.rate_limit import RateLimiterDep
from app.schemas.account import (
    DeleteAccountRequest,
    DomainConfigUpdate,
    DomainConfigUpdateResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.tenant import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from app.services.account_service import AccountServiceDep
from app.services.embedding.dependencies import VectorStoreDep
from app.services.tenant_service import TenantService

# Brute-force protection for password login, per email and per client address.
LOGIN_ATTEMPTS_PER_EMAIL = 10
LOGIN_ATTEMPTS_PER_CLIENT = 30

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(
    prefix="/me", tags=["account"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


@auth_router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant with a dashboard password and its first API key",
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
async def register(payload: RegisterRequest, service: AccountServiceDep) -> RegisterResponse:
    tenant, api_key, plain_key = await service.register(payload)
    return RegisterResponse(
        tenant=MeResponse.model_validate(tenant),
        api_key=plain_key,
        key=ApiKeyResponse.model_validate(api_key),
    )


@auth_router.post(
    "/login",
    summary="Exchange email and password for a 7-day dashboard session key",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid email or password"},
        429: {"model": ErrorResponse, "description": "Too many attempts"},
    },
)
async def login(
    payload: LoginRequest, request: Request, service: AccountServiceDep, limiter: RateLimiterDep
) -> LoginResponse:
    client = request.client.host if request.client else "unknown"
    await limiter.hit(f"login:client:{client}", LOGIN_ATTEMPTS_PER_CLIENT)
    await limiter.hit(f"login:email:{payload.email}", LOGIN_ATTEMPTS_PER_EMAIL)
    tenant, session_key, plain_key = await service.login(payload.email, payload.password)
    assert session_key.expires_at is not None
    return LoginResponse(
        tenant=MeResponse.model_validate(tenant),
        api_key=plain_key,
        expires_at=session_key.expires_at,
    )


@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current dashboard session key (integration keys are untouched)",
    dependencies=PROTECTED,
    responses=AUTH_RESPONSES,
)
async def logout(auth: AuthDep, service: AccountServiceDep) -> None:
    await service.logout(auth.api_key)


# --- Signed-in tenant ---


@me_router.get("", summary="The signed-in tenant")
async def get_me(auth: AuthDep) -> MeResponse:
    return MeResponse.model_validate(auth.tenant)


@me_router.put(
    "/domain-config",
    summary="Replace the domain config",
    responses={422: {"model": ErrorResponse, "description": "Invalid config"}},
)
async def update_domain_config(
    payload: DomainConfigUpdate, auth: AuthDep, service: AccountServiceDep
) -> DomainConfigUpdateResponse:
    rebuild = await service.update_domain_config(auth.tenant, payload.domain_config)
    return DomainConfigUpdateResponse(
        tenant=MeResponse.model_validate(auth.tenant), rebuild_recommended=rebuild
    )


@me_router.post(
    "/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete this tenant, its items, keys, logs and vector index",
)
async def delete_account(
    payload: DeleteAccountRequest,
    auth: AuthDep,
    service: AccountServiceDep,
    vector_store: VectorStoreDep,
) -> None:
    await service.delete_account(auth.tenant, payload.confirm_email, payload.password, vector_store)


@me_router.get("/api-keys", summary="API keys (dashboard session keys are not listed)")
async def list_api_keys(auth: AuthDep, service: AccountServiceDep) -> list[ApiKeyResponse]:
    return [ApiKeyResponse.model_validate(k) for k in await service.list_api_keys(auth.tenant)]


@me_router.post(
    "/api-keys",
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key (the plain key is returned only once)",
)
async def create_api_key(
    payload: ApiKeyCreate, auth: AuthDep, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiKeyCreatedResponse:
    api_key, plain_key = await TenantService(session).create_api_key(auth.tenant.id, payload)
    return ApiKeyCreatedResponse.model_validate(
        {**ApiKeyResponse.model_validate(api_key).model_dump(), "api_key": plain_key}
    )


@me_router.delete(
    "/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke an API key"
)
async def revoke_api_key(
    key_id: uuid.UUID, auth: AuthDep, session: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    await TenantService(session).revoke_api_key(auth.tenant.id, key_id)
