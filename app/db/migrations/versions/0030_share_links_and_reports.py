"""Public trip share links and content reports (moderation).

Revision ID: 0030_share_links_and_reports
Revises: 0029_guest_mode
Create Date: 2026-10-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_share_links_and_reports"
down_revision: Union[str, None] = "0029_guest_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base():
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "trip_share_links", *_base(),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(20), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_trip_share_links_trip_id", "trip_share_links", ["trip_id"])
    op.create_index("ix_trip_share_links_token_hash", "trip_share_links", ["token_hash"], unique=True)
    op.create_table(
        "content_reports", *_base(),
        sa.Column("link_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trip_share_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_title", sa.String(255), nullable=True),
        sa.Column("trip_destination", sa.String(255), nullable=True),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("details", sa.String(500), nullable=True),
        sa.Column("reporter_ip_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(500), nullable=True),
    )
    op.create_index("ix_content_reports_link_id", "content_reports", ["link_id"])
    op.create_index("ix_content_reports_trip_id", "content_reports", ["trip_id"])
    op.create_index("ix_content_reports_status", "content_reports", ["status"])
    op.create_index("ix_content_reports_reporter_ip_hash", "content_reports", ["reporter_ip_hash"])


def downgrade() -> None:
    op.drop_table("content_reports")
    op.drop_table("trip_share_links")
