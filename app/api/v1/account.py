"""Dashboard sign-up/sign-in (`/auth`) and self-service for the signed-in tenant (`/me`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.config import settings
from app.core.database import get_db
from app.core.email import send_email
from app.core.exceptions import ForbiddenError, RateLimitError, UnauthorizedError
from app.middleware.auth import AuthDep
from app.middleware.rate_limit import RateLimiterDep
from app.schemas.account import (
    DeleteAccountRequest,
    DomainConfigUpdate,
    DomainConfigUpdateResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MessageResponse,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from app.schemas.common import ErrorResponse
from app.schemas.tenant import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from app.services.account_emails import (
    password_changed_email,
    password_reset_email,
    verification_email,
)
from app.services.account_service import AccountServiceDep
from app.services.embedding.dependencies import VectorStoreDep
from app.services.tenant_service import TenantService

# Brute-force protection for password login, per email and per client address.
LOGIN_ATTEMPTS_PER_EMAIL = 10
LOGIN_ATTEMPTS_PER_CLIENT = 30
# Abuse limits for the public account endpoints, per minute.
REGISTRATIONS_PER_CLIENT = 5
RESET_REQUESTS_PER_CLIENT = 5
RESET_REQUESTS_PER_EMAIL = 2
LINK_ATTEMPTS_PER_CLIENT = 20
VERIFICATION_EMAILS_PER_TENANT = 1

FORGOT_PASSWORD_MESSAGE = (
    "If an account exists for that email, a password reset link is on its way. "
    "It expires in {minutes} minutes."
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(
    prefix="/me", tags=["account"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _locked(retry_after: int) -> RateLimitError:
    minutes = max(round(retry_after / 60), 1)
    unit = "minute" if minutes == 1 else "minutes"
    return RateLimitError(
        f"Too many failed sign-in attempts. Try again in {minutes} {unit}, or reset your password.",
        retry_after=retry_after,
    )


@auth_router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant with a dashboard password and sign it in",
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered"},
        429: {"model": ErrorResponse, "description": "Too many sign-ups from this address"},
    },
)
async def register(
    payload: RegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
) -> RegisterResponse:
    await limiter.hit(f"register:client:{_client(request)}", REGISTRATIONS_PER_CLIENT)
    tenant, session_key, plain_key, token = await service.register(payload)
    background_tasks.add_task(send_email, verification_email(tenant, token))
    assert session_key.expires_at is not None
    return RegisterResponse(
        tenant=MeResponse.model_validate(tenant),
        api_key=plain_key,
        expires_at=session_key.expires_at,
        verification_required=settings.REQUIRE_EMAIL_VERIFICATION and not tenant.email_verified,
    )


@auth_router.post(
    "/login",
    summary="Exchange email and password for a 7-day dashboard session key",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid email or password"},
        429: {"model": ErrorResponse, "description": "Too many attempts, or account locked"},
    },
)
async def login(
    payload: LoginRequest, request: Request, service: AccountServiceDep, limiter: RateLimiterDep
) -> LoginResponse:
    await limiter.hit(f"login:client:{_client(request)}", LOGIN_ATTEMPTS_PER_CLIENT)
    await limiter.hit(f"login:email:{payload.email}", LOGIN_ATTEMPTS_PER_EMAIL)
    lock = f"login:{payload.email}"
    if await limiter.failures(lock) >= settings.LOGIN_MAX_FAILURES:
        raise _locked(await limiter.retry_after(lock))
    try:
        tenant, session_key, plain_key = await service.login(payload.email, payload.password)
    except UnauthorizedError:
        failures = await limiter.record_failure(lock, settings.LOGIN_LOCKOUT_MINUTES * 60)
        if failures >= settings.LOGIN_MAX_FAILURES:
            raise _locked(await limiter.retry_after(lock)) from None
        raise
    await limiter.clear_failures(lock)
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


@auth_router.post(
    "/verify-email",
    summary="Confirm the account email with the token from the verification link",
    responses={400: {"model": ErrorResponse, "description": "Invalid, used or expired link"}},
)
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
) -> MeResponse:
    await limiter.hit(f"link:client:{_client(request)}", LINK_ATTEMPTS_PER_CLIENT)
    return MeResponse.model_validate(await service.verify_email(payload.token))


@auth_router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a new verification link to the signed-in tenant",
    dependencies=PROTECTED,
    responses={
        **AUTH_RESPONSES,
        409: {"model": ErrorResponse, "description": "Already verified"},
    },
)
async def resend_verification(
    auth: AuthDep,
    background_tasks: BackgroundTasks,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
) -> MessageResponse:
    await limiter.hit(f"verify-email:{auth.tenant.id}", VERIFICATION_EMAILS_PER_TENANT)
    token = await service.resend_verification(auth.tenant)
    background_tasks.add_task(send_email, verification_email(auth.tenant, token))
    return MessageResponse(message=f"Verification link sent to {auth.tenant.email}.")


@auth_router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset link (same answer whether or not the account exists)",
    responses={429: {"model": ErrorResponse, "description": "Too many requests"}},
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
) -> MessageResponse:
    await limiter.hit(f"reset:client:{_client(request)}", RESET_REQUESTS_PER_CLIENT)
    await limiter.hit(f"reset:email:{payload.email}", RESET_REQUESTS_PER_EMAIL)
    issued = await service.request_password_reset(payload.email)
    if issued is not None:
        tenant, token = issued
        background_tasks.add_task(send_email, password_reset_email(tenant, token))
    return MessageResponse(
        message=FORGOT_PASSWORD_MESSAGE.format(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
    )


@auth_router.post(
    "/reset-password",
    summary="Set a new password with the token from the reset link; signs out all sessions",
    responses={400: {"model": ErrorResponse, "description": "Invalid, used or expired link"}},
)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
) -> MessageResponse:
    await limiter.hit(f"link:client:{_client(request)}", LINK_ATTEMPTS_PER_CLIENT)
    tenant = await service.reset_password(payload.token, payload.password)
    await limiter.clear_failures(f"login:{tenant.email}")
    background_tasks.add_task(send_email, password_changed_email(tenant))
    return MessageResponse(message="Password updated. Sign in with your new password.")


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
    responses={403: {"model": ErrorResponse, "description": "Email not verified yet"}},
)
async def create_api_key(
    payload: ApiKeyCreate, auth: AuthDep, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiKeyCreatedResponse:
    if settings.REQUIRE_EMAIL_VERIFICATION and not auth.tenant.email_verified:
        raise ForbiddenError(
            "Verify your email address before creating API keys. Open the link we emailed "
            "you, or request a new one with POST /auth/resend-verification."
        )
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
