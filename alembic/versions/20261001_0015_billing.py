"""billing: workspace plan and Stripe subscription

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

billing_plan = sa.Enum("FREE", "PRO", name="billing_plan")


def upgrade() -> None:
    billing_plan.create(op.get_bind(), checkfirst=True)
    op.add_column("tenants", sa.Column("plan", billing_plan, server_default="FREE", nullable=False))
    op.add_column("tenants", sa.Column("stripe_customer_id", sa.String(255), nullable=True))
    op.add_column("tenants", sa.Column("stripe_subscription_id", sa.String(255), nullable=True))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(32), nullable=True))
    op.add_column(
        "tenants", sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tenants",
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_unique_constraint(
        op.f("uq_tenants_stripe_customer_id"), "tenants", ["stripe_customer_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_tenants_stripe_customer_id"), "tenants", type_="unique")
    for column in (
        "cancel_at_period_end",
        "current_period_end",
        "subscription_status",
        "stripe_subscription_id",
        "stripe_customer_id",
        "plan",
    ):
        op.drop_column("tenants", column)
    billing_plan.drop(op.get_bind(), checkfirst=True)
