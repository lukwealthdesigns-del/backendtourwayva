"""Paystack refunds and chargebacks (item-5 hardening):

  * payment_status_enum gains 'refunded' and 'disputed'
  * payments.refunded_amount_minor / payments.refunded_at — cumulative refund
    tracking so partial refunds are representable and revenue analytics can
    report NET revenue rather than gross

Revision ID: 0024_payment_refunds_disputes
Revises: 0023_analytics_cost_and_uploads
Create Date: 2026-09-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_payment_refunds_disputes"
down_revision: Union[str, None] = "0023_analytics_cost_and_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL versions, and a new value can't be used in the transaction
    # that adds it — autocommit_block() sidesteps both.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_status_enum ADD VALUE IF NOT EXISTS 'refunded'")
        op.execute("ALTER TYPE payment_status_enum ADD VALUE IF NOT EXISTS 'disputed'")

    op.add_column(
        "payments",
        sa.Column("refunded_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("payments", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "refunded_at")
    op.drop_column("payments", "refunded_amount_minor")
    # PostgreSQL cannot drop a single enum value. Rows may already use
    # 'refunded'/'disputed', so the enum values are deliberately left in place;
    # they are harmless once the columns above are gone.
