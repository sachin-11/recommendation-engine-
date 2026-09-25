"""Dashboard account schemas: registration, login, users and roles, the team, the current
workspace and its settings."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models.tenant import Tenant
from app.models.user import Role, User
from app.schemas.tenant import DomainConfig, NonBlankStr, TenantCreate, TenantResponse

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
LinkToken = Annotated[str, StringConstraints(min_length=16, max_length=128)]
PersonName = Annotated[NonBlankStr, StringConstraints(max_length=255)]


class RegisterRequest(TenantCreate):
    password: Password
    owner_name: PersonName | None = Field(
        default=None, description="Your name; defaults to the business name."
    )


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


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: Role
    email_verified: bool
    has_password: bool
    last_login_at: datetime | None
    created_at: datetime


class MeResponse(TenantResponse):
    """The workspace, plus who is asking: a signed-in user, or an integration key."""

    role: Role = Field(description="The caller's role. Integration API keys act as DEVELOPER.")
    user: UserResponse | None = Field(description="Null when calling with an integration key.")
    has_password: bool
    email_verified: bool = Field(
        description="The signed-in user's email (the owner's, for an integration key)."
    )

    @classmethod
    def build(cls, tenant: Tenant, user: User | None, role: Role) -> "MeResponse":
        base = TenantResponse.model_validate(tenant).model_dump()
        return cls(
            **base,
            role=role,
            user=UserResponse.model_validate(user) if user else None,
            has_password=user.has_password if user else False,
            email_verified=user.email_verified if user else tenant.email_verified,
        )


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


# --- Team ---


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Role = Role.DEVELOPER

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("role")
    @classmethod
    def _not_owner(cls, value: Role) -> Role:
        if value == Role.OWNER:
            raise ValueError("Invite as ADMIN, DEVELOPER or VIEWER; transfer ownership separately")
        return value


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    invited_by_id: uuid.UUID | None
    expires_at: datetime
    created_at: datetime


class TeamResponse(BaseModel):
    members: list[UserResponse]
    invitations: list[InvitationResponse] = Field(description="Pending invitations.")


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role

    @field_validator("role")
    @classmethod
    def _not_owner(cls, value: Role) -> Role:
        if value == Role.OWNER:
            raise ValueError("Use /me/members/transfer-ownership to make someone the owner")
        return value


class TransferOwnershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    password: str | None = Field(default=None, description="Your password, if you have one.")


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: LinkToken


class InvitationInfo(BaseModel):
    workspace_name: str
    email: str
    role: Role
    invited_by: str | None
    expires_at: datetime


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: LinkToken
    name: PersonName
    password: Password
