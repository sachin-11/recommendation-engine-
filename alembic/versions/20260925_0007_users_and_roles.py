"""users with roles, invitations; sign-in moves from tenants to users

Every existing tenant gets an OWNER user with the tenant's email, name, password and
verification date. Session keys are attached to that owner; pending email links are
dropped (users can request new ones).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("VIEWER", "DEVELOPER", "ADMIN", "OWNER")


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    role = sa.Enum(*ROLES, name="user_role")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_users_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        # The type already exists (created with users).
        sa.Column(
            "role", postgresql.ENUM(*ROLES, name="user_role", create_type=False), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_invitations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            name=op.f("fk_invitations_invited_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_tenant_id"), "invitations", ["tenant_id"])

    # One OWNER per existing tenant, carrying its sign-in details.
    bind = op.get_bind()
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("password_hash", sa.String()),
        sa.column("email_verified_at", sa.DateTime(timezone=True)),
    )
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("email", sa.String()),
        sa.column("name", sa.String()),
        sa.column("role", role),
        sa.column("password_hash", sa.String()),
        sa.column("email_verified_at", sa.DateTime(timezone=True)),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    owners = [
        {
            "id": uuid.uuid4(),
            "tenant_id": t.id,
            "email": t.email,
            "name": t.name,
            "role": "OWNER",
            "password_hash": t.password_hash,
            "email_verified_at": t.email_verified_at,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        for t in bind.execute(sa.select(tenants)).all()
    ]
    if owners:
        op.bulk_insert(users, owners)

    op.add_column("api_keys", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("api_keys", sa.Column("created_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_api_keys_user_id_users"),
        "api_keys",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_api_keys_created_by_id_users"),
        "api_keys",
        "users",
        ["created_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"])
    op.execute(
        "UPDATE api_keys SET user_id = (SELECT users.id FROM users "
        "WHERE users.tenant_id = api_keys.tenant_id AND users.role = 'OWNER') "
        "WHERE is_session"
    )

    # Link tokens now belong to users. They live at most a day, so drop the pending ones.
    op.execute("DELETE FROM auth_tokens")
    op.drop_index(op.f("ix_auth_tokens_tenant_id"), table_name="auth_tokens")
    op.drop_constraint(op.f("fk_auth_tokens_tenant_id_tenants"), "auth_tokens", type_="foreignkey")
    op.drop_column("auth_tokens", "tenant_id")
    op.add_column("auth_tokens", sa.Column("user_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        op.f("fk_auth_tokens_user_id_users"),
        "auth_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_auth_tokens_user_id"), "auth_tokens", ["user_id"])

    op.drop_column("tenants", "password_hash")


def downgrade() -> None:
    op.add_column("tenants", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.execute(
        "UPDATE tenants SET password_hash = (SELECT users.password_hash FROM users "
        "WHERE users.tenant_id = tenants.id AND users.role = 'OWNER')"
    )

    op.execute("DELETE FROM auth_tokens")
    op.drop_index(op.f("ix_auth_tokens_user_id"), table_name="auth_tokens")
    op.drop_constraint(op.f("fk_auth_tokens_user_id_users"), "auth_tokens", type_="foreignkey")
    op.drop_column("auth_tokens", "user_id")
    op.add_column("auth_tokens", sa.Column("tenant_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        op.f("fk_auth_tokens_tenant_id_tenants"),
        "auth_tokens",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_auth_tokens_tenant_id"), "auth_tokens", ["tenant_id"])

    op.drop_index(op.f("ix_api_keys_user_id"), table_name="api_keys")
    op.drop_constraint(op.f("fk_api_keys_created_by_id_users"), "api_keys", type_="foreignkey")
    op.drop_constraint(op.f("fk_api_keys_user_id_users"), "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "created_by_id")
    op.drop_column("api_keys", "user_id")

    op.drop_index(op.f("ix_invitations_tenant_id"), table_name="invitations")
    op.drop_table("invitations")
    op.drop_index(op.f("ix_users_tenant_id"), table_name="users")
    op.drop_table("users")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
