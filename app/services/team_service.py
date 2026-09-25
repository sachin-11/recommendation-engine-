"""Workspace members: invitations, roles, removal and ownership transfer.

Rules:
- There is exactly one OWNER. Ownership moves only by transfer; the old owner becomes ADMIN.
- ADMIN and OWNER manage the team. Nobody changes their own role, and the OWNER can
  neither be demoted nor removed.
- A person belongs to one workspace, so an email that already has a user cannot be invited.
- Accepting an invitation creates the user with a verified email (the link proved it).
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.passwords import hash_password, verify_password
from app.core.security import hash_api_key
from app.models.base import utcnow
from app.models.invitation import Invitation
from app.models.tenant import Tenant
from app.models.user import Role, User

INVALID_INVITATION = "This invitation is invalid, was revoked, or has expired. Ask for a new one."


def _as_aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class TeamService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Reading ---

    async def members(self, tenant: Tenant) -> list[User]:
        users = (await self._session.scalars(select(User).where(User.tenant_id == tenant.id))).all()
        return sorted(users, key=lambda u: (-u.role.rank, u.created_at))

    async def pending_invitations(self, tenant: Tenant) -> list[Invitation]:
        return list(
            (
                await self._session.scalars(
                    select(Invitation)
                    .where(
                        Invitation.tenant_id == tenant.id,
                        Invitation.accepted_at.is_(None),
                        Invitation.expires_at > utcnow(),
                    )
                    .order_by(Invitation.created_at.desc())
                )
            ).all()
        )

    # --- Invitations ---

    async def invite(
        self, tenant: Tenant, inviter: User | None, email: str, role: Role
    ) -> tuple[Invitation, str]:
        existing = await self._session.scalar(select(User).where(User.email == email))
        if existing is not None:
            where = "this" if existing.tenant_id == tenant.id else "another"
            raise ConflictError(f"{email} already belongs to {where} workspace")
        # Re-inviting replaces the earlier invitation, so only the newest link works.
        await self._session.execute(
            delete(Invitation).where(
                Invitation.tenant_id == tenant.id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
            )
        )
        plain = secrets.token_urlsafe(32)
        invitation = Invitation(
            tenant_id=tenant.id,
            email=email,
            role=role,
            token_hash=hash_api_key(plain),
            invited_by_id=inviter.id if inviter else None,
            expires_at=utcnow() + timedelta(days=settings.INVITATION_TTL_DAYS),
        )
        self._session.add(invitation)
        await self._session.commit()
        return invitation, plain

    async def revoke_invitation(self, tenant: Tenant, invitation_id: uuid.UUID) -> None:
        result = await self._session.execute(
            delete(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.tenant_id == tenant.id,
                Invitation.accepted_at.is_(None),
            )
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            raise NotFoundError(f"Invitation '{invitation_id}' not found")
        await self._session.commit()

    async def open_invitation(self, token: str) -> tuple[Invitation, Tenant, User | None]:
        invitation = await self._session.scalar(
            select(Invitation).where(Invitation.token_hash == hash_api_key(token))
        )
        if (
            invitation is None
            or invitation.accepted_at is not None
            or _as_aware(invitation.expires_at) <= utcnow()
        ):
            raise BadRequestError(INVALID_INVITATION)
        tenant = await self._session.get(Tenant, invitation.tenant_id)
        if tenant is None or not tenant.is_active:
            raise BadRequestError(INVALID_INVITATION)
        inviter = (
            await self._session.get(User, invitation.invited_by_id)
            if invitation.invited_by_id
            else None
        )
        return invitation, tenant, inviter

    async def accept(self, token: str, name: str, password: str) -> tuple[Tenant, User]:
        invitation, tenant, _ = await self.open_invitation(token)
        if await self._session.scalar(select(exists().where(User.email == invitation.email))):
            raise ConflictError(f"{invitation.email} already has an account")
        now = utcnow()
        user = User(
            tenant_id=tenant.id,
            email=invitation.email,
            name=name,
            role=invitation.role,
            password_hash=hash_password(password),
            email_verified_at=now,
        )
        invitation.accepted_at = now
        self._session.add(user)
        await self._session.commit()
        return tenant, user

    # --- Members ---

    async def _member(self, tenant: Tenant, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None or user.tenant_id != tenant.id:
            raise NotFoundError(f"Member '{user_id}' not found")
        return user

    async def change_role(
        self, tenant: Tenant, actor: User | None, user_id: uuid.UUID, role: Role
    ) -> User:
        member = await self._member(tenant, user_id)
        if actor is not None and member.id == actor.id:
            raise ForbiddenError("You cannot change your own role")
        if member.role == Role.OWNER:
            raise ForbiddenError("The owner's role changes only by transferring ownership")
        member.role = role
        await self._session.commit()
        return member

    async def remove(self, tenant: Tenant, user_id: uuid.UUID) -> None:
        """Remove a member (their sessions go with them; keys they created stay)."""
        member = await self._member(tenant, user_id)
        if member.role == Role.OWNER:
            raise ForbiddenError("The owner cannot be removed; transfer ownership first")
        await self._session.delete(member)
        await self._session.commit()

    async def transfer_ownership(
        self, tenant: Tenant, owner: User, user_id: uuid.UUID, password: str | None
    ) -> User:
        if owner.has_password and not verify_password(password or "", owner.password_hash):
            raise ForbiddenError("Password is incorrect")
        member = await self._member(tenant, user_id)
        if member.id == owner.id:
            raise BadRequestError("You are already the owner")
        if not (member.is_active and member.email_verified):
            raise BadRequestError("The new owner must be an active member with a verified email")
        member.role = Role.OWNER
        owner.role = Role.ADMIN
        # The workspace email follows the owner (it is where account email goes).
        tenant.email = member.email
        tenant.email_verified_at = member.email_verified_at
        await self._session.commit()
        return member


def get_team_service(session: Annotated[AsyncSession, Depends(get_db)]) -> TeamService:
    return TeamService(session)


TeamServiceDep = Annotated[TeamService, Depends(get_team_service)]
