"""index user_feedback by end user, for personalization

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_user_feedback_tenant_id_end_user_id",
        "user_feedback",
        ["tenant_id", "end_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_feedback_tenant_id_end_user_id", table_name="user_feedback")
