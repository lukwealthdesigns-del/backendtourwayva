"""provider_usage, api_usage, geocoding_cache, currency_cache (Master Prompt §5, §78)

Revision ID: 0020_usage_and_durable_cache
Revises: 0019_workers
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_usage_and_durable_cache"
down_revision: Union[str, None] = "0019_workers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_columns():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "provider_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("circuit_state", sa.String(20), nullable=False, server_default="closed"),
        sa.Column("error_type", sa.String(100), nullable=True),
        *_ts_columns(),
    )
    op.create_index("ix_provider_usage_provider", "provider_usage", ["provider"])
    op.create_index("ix_provider_usage_created_at", "provider_usage", ["created_at"])

    op.create_table(
        "api_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_api_usage_user_id", "api_usage", ["user_id"])
    op.create_index("ix_api_usage_path", "api_usage", ["path"])
    op.create_index("ix_api_usage_created_at", "api_usage", ["created_at"])

    op.create_table(
        "geocoding_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("cache_key", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("response", postgresql.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_ts_columns(),
    )
    op.create_index("ix_geocoding_cache_cache_key", "geocoding_cache", ["cache_key"], unique=True)

    op.create_table(
        "currency_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("target", sa.String(3), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_ts_columns(),
    )
    op.create_index("ix_currency_cache_base", "currency_cache", ["base"])
    op.create_index("ix_currency_cache_target", "currency_cache", ["target"])
    # Common lookup: latest still-valid rate for a pair.
    op.create_index("ix_currency_cache_pair_fetched", "currency_cache", ["base", "target", "fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_currency_cache_pair_fetched", table_name="currency_cache")
    op.drop_index("ix_currency_cache_target", table_name="currency_cache")
    op.drop_index("ix_currency_cache_base", table_name="currency_cache")
    op.drop_table("currency_cache")

    op.drop_index("ix_geocoding_cache_cache_key", table_name="geocoding_cache")
    op.drop_table("geocoding_cache")

    op.drop_index("ix_api_usage_created_at", table_name="api_usage")
    op.drop_index("ix_api_usage_path", table_name="api_usage")
    op.drop_index("ix_api_usage_user_id", table_name="api_usage")
    op.drop_table("api_usage")

    op.drop_index("ix_provider_usage_created_at", table_name="provider_usage")
    op.drop_index("ix_provider_usage_provider", table_name="provider_usage")
    op.drop_table("provider_usage")
