"""admin_users, admin_audit_logs, admin_messages, broadcast_jobs, users.sessions_invalidated_at

Revision ID: 0009_admin
Revises: 0008_monetization
Create Date: 2026-09-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_admin"
down_revision: Union[str, None] = "0008_monetization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN_ROLE_ENUM = sa.Enum(
    "super_admin", "admin", "support_admin", "content_admin", "finance_admin",
    "analytics_admin", "moderation_admin", name="admin_role_enum",
)
_CHANNEL_ENUM_MSG = sa.Enum("in_app", "email", "both", name="admin_message_channel_enum")
_CHANNEL_ENUM_BROADCAST = sa.Enum("in_app", "email", "both", name="broadcast_channel_enum")


def upgrade() -> None:
    op.add_column("users", sa.Column("sessions_invalidated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", _ADMIN_ROLE_ENUM, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("user_id", name="uq_admin_users_user_id"),
    )
    op.create_index("ix_admin_users_user_id", "admin_users", ["user_id"])

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("result", sa.Enum("success", "failure", name="audit_result_enum"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"]),
    )
    op.create_index("ix_admin_audit_logs_admin_user_id", "admin_audit_logs", ["admin_user_id"])

    op.create_table(
        "admin_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("sender_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", _CHANNEL_ENUM_MSG, nullable=False),
        sa.Column("status", sa.Enum("sent", "failed", name="admin_message_status_enum"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sender_admin_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_admin_messages_recipient_user_id", "admin_messages", ["recipient_user_id"])

    op.create_table(
        "broadcast_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("sender_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "segment",
            sa.Enum("all", "free", "trial", "premium", "inactive", name="broadcast_segment_enum"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", _CHANNEL_ENUM_BROADCAST, nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="broadcast_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("recipient_count", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sender_admin_id"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("broadcast_jobs")

    op.drop_index("ix_admin_messages_recipient_user_id", table_name="admin_messages")
    op.drop_table("admin_messages")

    op.drop_index("ix_admin_audit_logs_admin_user_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    op.drop_index("ix_admin_users_user_id", table_name="admin_users")
    op.drop_table("admin_users")

    op.drop_column("users", "sessions_invalidated_at")

    sa.Enum(name="broadcast_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="broadcast_segment_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="broadcast_channel_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="admin_message_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="admin_message_channel_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="audit_result_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="admin_role_enum").drop(op.get_bind(), checkfirst=True)
