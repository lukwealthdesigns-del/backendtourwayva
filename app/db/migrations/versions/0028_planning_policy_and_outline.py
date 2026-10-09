"""Long-trip planning:

  * planning_policy — admin-controlled limits (growth mode, max days per tier, chunk size, monthly generation caps,
    per-trip AI cost alert). One row is seeded here with launch defaults (growth mode ON).
  * trips.planning_outline (JSONB, nullable) — the whole-trip route and which parts have a detailed plan. NULL for
    ordinary trips.

Revision ID: 0028_planning_policy_and_outline
Revises: 0027_message_meta
Create Date: 2026-10-08

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_planning_policy_and_outline"
down_revision: Union[str, None] = "0027_message_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("planning_outline", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    table = op.create_table(
        "planning_policy",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("growth_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("growth_max_days", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("premium_max_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("free_max_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("full_detail_max_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("chunk_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("growth_monthly_generations", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("premium_monthly_generations", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("free_monthly_generations", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("cost_alert_usd", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.bulk_insert(table, [{"id": uuid.uuid4()}])      # one row with the launch defaults above (growth mode ON)


def downgrade() -> None:
    op.drop_table("planning_policy")
    op.drop_column("trips", "planning_outline")
