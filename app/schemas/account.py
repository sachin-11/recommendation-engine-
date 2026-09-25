"""Dashboard account schemas: registration, login, the current tenant and its settings."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.schemas.tenant import DomainConfig, TenantCreate, TenantResponse

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
LinkToken = Annotated[str, StringConstraints(min_length=16, max_length=128)]


class RegisterRequest(TenantCreate):
    password: Password


class _EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.lower()


class LoginRequest(_EmailRequest):
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ForgotPasswordRequest(_EmailRequest):
    pass


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: LinkToken = Field(description="The token from the reset link.")
    password: Password


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: LinkToken = Field(description="The token from the verification link.")


class MessageResponse(BaseModel):
    message: str


class MeResponse(TenantResponse):
    has_password: bool
    email_verified: bool


class RegisterResponse(BaseModel):
    tenant: MeResponse
    api_key: str = Field(
        description="A 7-day dashboard session key; send it as X-API-Key. Create integration "
        "keys with POST /me/api-keys once the email is verified."
    )
    expires_at: datetime
    verification_required: bool = Field(
        description="True until the emailed link is opened; API keys cannot be created until then."
    )


class LoginResponse(BaseModel):
    tenant: MeResponse
    api_key: str = Field(description="A dashboard session key; send it as X-API-Key.")
    expires_at: datetime


class DomainConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_config: DomainConfig


class DomainConfigUpdateResponse(BaseModel):
    tenant: MeResponse
    rebuild_recommended: bool = Field(
        description="True when embedded text or stored filters changed; run /index/rebuild."
    )


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_email: EmailStr = Field(description="Must equal the account email.")
    password: str | None = Field(default=None, description="Required if the account has one.")


class BulkDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=255)]] = Field(
        min_length=1, max_length=1000
    )


class BulkDeleteResponse(BaseModel):
    deleted: int
    not_found: list[str]
