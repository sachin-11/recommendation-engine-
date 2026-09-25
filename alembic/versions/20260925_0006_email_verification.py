"""email verification and password reset: tenants.email_verified_at and auth_tokens

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSES = ("VERIFY_EMAIL", "RESET_PASSWORD")


def upgrade() -> None:
    op.add_column(
        "tenants", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Accounts that existed before verification was introduced keep working.
    op.execute("UPDATE tenants SET email_verified_at = created_at")

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Enum(*PURPOSES, name="auth_token_purpose"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_auth_tokens_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_tokens_token_hash")),
    )
    op.create_index(op.f("ix_auth_tokens_tenant_id"), "auth_tokens", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_tokens_tenant_id"), table_name="auth_tokens")
    op.drop_table("auth_tokens")
    sa.Enum(name="auth_token_purpose").drop(op.get_bind(), checkfirst=True)
    op.drop_column("tenants", "email_verified_at")
