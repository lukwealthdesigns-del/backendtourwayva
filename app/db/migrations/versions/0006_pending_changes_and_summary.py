"""pending_itinerary_changes, conversations.summary

Revision ID: 0006_pending_changes_and_summary
Revises: 0005_rag_knowledge
Create Date: 2026-09-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_pending_changes_and_summary"
down_revision: Union[str, None] = "0005_rag_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))

    op.create_table(
        "pending_itinerary_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "action",
            sa.Enum("add_item", "update_item", "delete_item", name="pending_change_action_enum"),
            nullable=False,
        ),
        sa.Column("day_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSON(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "confirmed", "rejected", name="pending_change_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("proposed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["day_id"], ["trip_days.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["trip_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
    )
    op.create_index("ix_pending_itinerary_changes_conversation_id", "pending_itinerary_changes", ["conversation_id"])
    op.create_index("ix_pending_itinerary_changes_trip_id", "pending_itinerary_changes", ["trip_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_itinerary_changes_trip_id", table_name="pending_itinerary_changes")
    op.drop_index("ix_pending_itinerary_changes_conversation_id", table_name="pending_itinerary_changes")
    op.drop_table("pending_itinerary_changes")
    sa.Enum(name="pending_change_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="pending_change_action_enum").drop(op.get_bind(), checkfirst=True)

    op.drop_column("conversations", "summary")
