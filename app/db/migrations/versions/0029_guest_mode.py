"""Guest mode: users.is_guest and the guest_sessions table.

Revision ID: 0029_guest_mode
Revises: 0028_planning_policy_and_outline
Create Date: 2026-10-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_guest_mode"
down_revision: Union[str, None] = "0028_planning_policy_and_outline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_users_is_guest", "users", ["is_guest"])
    op.create_table(
        "guest_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("guest_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("companion_messages_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("claim_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint("guest_user_id"),
    )
    op.create_index("ix_guest_sessions_token_hash", "guest_sessions", ["token_hash"], unique=True)
    op.create_index("ix_guest_sessions_status", "guest_sessions", ["status"])
    op.create_index("ix_guest_sessions_expires_at", "guest_sessions", ["expires_at"])
    op.create_index("ix_guest_sessions_ip_hash", "guest_sessions", ["ip_hash"])


def downgrade() -> None:
    op.drop_table("guest_sessions")
    op.drop_index("ix_users_is_guest", table_name="users")
    op.drop_column("users", "is_guest")
