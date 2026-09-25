"""Team members (`/me/members`, Admin to change) and public invitation links (`/auth`)."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Request, status

from app.api.v1.account import ROLE_RESPONSE, client_address, login_response
from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.email import send_email
from app.core.exceptions import BadRequestError
from app.middleware.auth import AuthDep, require_role
from app.middleware.rate_limit import RateLimiterDep
from app.models.user import Role
from app.schemas.account import (
    AcceptInvitationRequest,
    InvitationInfo,
    InvitationResponse,
    InvitationTokenRequest,
    InviteRequest,
    LoginResponse,
    RoleUpdate,
    TeamResponse,
    TransferOwnershipRequest,
    UserResponse,
)
from app.schemas.common import ErrorResponse
from app.services.account_emails import invitation_email
from app.services.account_service import AccountServiceDep
from app.services.team_service import TeamServiceDep

INVITATIONS_PER_MINUTE = 10
LINK_ATTEMPTS_PER_CLIENT = 20

members_router = APIRouter(
    prefix="/me/members", tags=["team"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)
invitations_router = APIRouter(prefix="/auth/invitations", tags=["team"])

ADMIN = [require_role(Role.ADMIN)]


@members_router.get("", summary="Members and pending invitations")
async def list_team(auth: AuthDep, team: TeamServiceDep) -> TeamResponse:
    return TeamResponse(
        members=[UserResponse.model_validate(u) for u in await team.members(auth.tenant)],
        invitations=[
            InvitationResponse.model_validate(i)
            for i in await team.pending_invitations(auth.tenant)
        ],
    )


@members_router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone by email (Admin)",
    dependencies=ADMIN,
    responses={
        409: {"model": ErrorResponse, "description": "Already has an account"},
        **ROLE_RESPONSE,
    },
)
async def invite(
    payload: InviteRequest,
    auth: AuthDep,
    team: TeamServiceDep,
    background_tasks: BackgroundTasks,
    limiter: RateLimiterDep,
) -> InvitationResponse:
    await limiter.hit(f"invite:{auth.tenant.id}", INVITATIONS_PER_MINUTE)
    invitation, token = await team.invite(auth.tenant, auth.user, payload.email, payload.role)
    background_tasks.add_task(
        send_email, invitation_email(invitation, auth.tenant, auth.user, token)
    )
    return InvitationResponse.model_validate(invitation)


@members_router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation (Admin)",
    dependencies=ADMIN,
    responses=ROLE_RESPONSE,
)
async def revoke_invitation(invitation_id: uuid.UUID, auth: AuthDep, team: TeamServiceDep) -> None:
    await team.revoke_invitation(auth.tenant, invitation_id)


@members_router.patch(
    "/{user_id}",
    summary="Change a member's role (Admin)",
    dependencies=ADMIN,
    responses=ROLE_RESPONSE,
)
async def change_role(
    user_id: uuid.UUID, payload: RoleUpdate, auth: AuthDep, team: TeamServiceDep
) -> UserResponse:
    return UserResponse.model_validate(
        await team.change_role(auth.tenant, auth.user, user_id, payload.role)
    )


@members_router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (Admin); their dashboard sessions end, API keys stay",
    dependencies=ADMIN,
    responses=ROLE_RESPONSE,
)
async def remove_member(user_id: uuid.UUID, auth: AuthDep, team: TeamServiceDep) -> None:
    await team.remove(auth.tenant, user_id)


@members_router.post(
    "/transfer-ownership",
    summary="Make another member the owner (Owner); you become an Admin",
    dependencies=[require_role(Role.OWNER)],
    responses=ROLE_RESPONSE,
)
async def transfer_ownership(
    payload: TransferOwnershipRequest, auth: AuthDep, team: TeamServiceDep
) -> UserResponse:
    if auth.user is None:  # require_role(OWNER) already rules out integration keys
        raise BadRequestError("Sign in to the dashboard to transfer ownership")
    return UserResponse.model_validate(
        await team.transfer_ownership(auth.tenant, auth.user, payload.user_id, payload.password)
    )


# --- Public: opening and accepting an invitation link ---


@invitations_router.post(
    "/lookup",
    summary="Who invited whom, to which workspace, from an invitation token",
    responses={400: {"model": ErrorResponse, "description": "Invalid, revoked or expired"}},
)
async def lookup_invitation(
    payload: InvitationTokenRequest,
    request: Request,
    team: TeamServiceDep,
    limiter: RateLimiterDep,
) -> InvitationInfo:
    await limiter.hit(f"link:client:{client_address(request)}", LINK_ATTEMPTS_PER_CLIENT)
    invitation, tenant, inviter = await team.open_invitation(payload.token)
    return InvitationInfo(
        workspace_name=tenant.name,
        email=invitation.email,
        role=invitation.role,
        invited_by=inviter.name if inviter else None,
        expires_at=invitation.expires_at,
    )


@invitations_router.post(
    "/accept",
    status_code=status.HTTP_201_CREATED,
    summary="Accept an invitation: create your user and sign in",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid, revoked or expired"},
        409: {"model": ErrorResponse, "description": "The email already has an account"},
    },
)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    request: Request,
    team: TeamServiceDep,
    accounts: AccountServiceDep,
    limiter: RateLimiterDep,
) -> LoginResponse:
    await limiter.hit(f"link:client:{client_address(request)}", LINK_ATTEMPTS_PER_CLIENT)
    tenant, user = await team.accept(payload.token, payload.name, payload.password)
    return login_response(await accounts.start_session(tenant, user))
