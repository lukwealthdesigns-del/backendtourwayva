"""discovery_searches, discovery_results

Revision ID: 0012_discover
Revises: 0011_attachments_travel_history
Create Date: 2026-09-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_discover"
down_revision: Union[str, None] = "0011_attachments_travel_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovery_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_params", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_discovery_searches_user_id", "discovery_searches", ["user_id"])

    op.create_table(
        "discovery_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("estimated_total_cost", sa.Float(), nullable=True),
        sa.Column("estimated_cost_currency", sa.String(3), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.String(1000), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["search_id"], ["discovery_searches.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_discovery_results_search_id", "discovery_results", ["search_id"])


def downgrade() -> None:
    op.drop_index("ix_discovery_results_search_id", table_name="discovery_results")
    op.drop_table("discovery_results")
    op.drop_index("ix_discovery_searches_user_id", table_name="discovery_searches")
    op.drop_table("discovery_searches")
