"""user_sessions (refresh rotation), user_preferences, account_deletion OTP purpose

Revision ID: 0013_sessions_preferences
Revises: 0012_discover
Create Date: 2026-09-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_sessions_preferences"
down_revision: Union[str, None] = "0012_discover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction block on
    # older PostgreSQL versions, hence the autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE otp_purpose_enum ADD VALUE IF NOT EXISTS 'account_deletion'")

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_jti_hash", sa.String(64), nullable=False),
        sa.Column("previous_jti_hash", sa.String(64), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_refresh_jti_hash", "user_sessions", ["refresh_jti_hash"], unique=True)
    op.create_index("ix_user_sessions_previous_jti_hash", "user_sessions", ["previous_jti_hash"])

    op.create_table(
        "user_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("travel_styles", sa.JSON(), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("budget_preference", sa.String(30), nullable=True),
        sa.Column("accommodation_preference", sa.String(50), nullable=True),
        sa.Column("transportation_preference", sa.String(50), nullable=True),
        sa.Column("walking_preference", sa.String(20), nullable=True),
        sa.Column("dietary_preferences", sa.JSON(), nullable=False),
        sa.Column("accessibility_preferences", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"], unique=True)

    op.create_index("ix_users_auth_provider_subject", "users", ["auth_provider", "provider_subject_id"])


def downgrade() -> None:
    op.drop_index("ix_users_auth_provider_subject", table_name="users")

    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")

    op.drop_index("ix_user_sessions_previous_jti_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_refresh_jti_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    # PostgreSQL cannot drop a single value from an enum type; the
    # 'account_deletion' value is intentionally left in place on downgrade.
