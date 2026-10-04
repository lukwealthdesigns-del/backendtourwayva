"""trip_invitations, trip_comments, pending_change_votes

Revision ID: 0007_collaboration
Revises: 0006_pending_changes_and_summary
Create Date: 2026-09-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_collaboration"
down_revision: Union[str, None] = "0006_pending_changes_and_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trip_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inviter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "editor", "contributor", "viewer", name="trip_invitation_role_enum"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "accepted", "rejected", "cancelled", "expired", name="invitation_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inviter_id"], ["users.id"]),
        sa.UniqueConstraint("token", name="uq_trip_invitations_token"),
    )
    op.create_index("ix_trip_invitations_trip_id", "trip_invitations", ["trip_id"])
    op.create_index("ix_trip_invitations_recipient_email", "trip_invitations", ["recipient_email"])
    op.create_index("ix_trip_invitations_token", "trip_invitations", ["token"])

    op.create_table(
        "trip_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_trip_comments_trip_id", "trip_comments", ["trip_id"])

    op.create_table(
        "pending_change_votes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("change_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_upvote", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["change_id"], ["pending_itinerary_changes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("change_id", "user_id", name="uq_pending_change_votes_change_user"),
    )
    op.create_index("ix_pending_change_votes_change_id", "pending_change_votes", ["change_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_change_votes_change_id", table_name="pending_change_votes")
    op.drop_table("pending_change_votes")

    op.drop_index("ix_trip_comments_trip_id", table_name="trip_comments")
    op.drop_table("trip_comments")

    op.drop_index("ix_trip_invitations_token", table_name="trip_invitations")
    op.drop_index("ix_trip_invitations_recipient_email", table_name="trip_invitations")
    op.drop_index("ix_trip_invitations_trip_id", table_name="trip_invitations")
    op.drop_table("trip_invitations")

    sa.Enum(name="invitation_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="trip_invitation_role_enum").drop(op.get_bind(), checkfirst=True)
