"""Admin analytics completeness (Master Blueprint §57-58, §89) + upload
tracking fix (Master Prompt §42):

  * ai_usage_records.trip_id       -> enables real cost-per-trip, not just
                                       cost-per-user (Blueprint §58)
  * booking_clicks (new table)     -> estimated affiliate revenue (Blueprint
                                       §57's "affiliate activity"; Master
                                       Prompt §5's booking_clicks/commissions)
  * attachments.category (new col) -> attachment | travel_document |
                                       trip_pdf. Lets every upload path write a
                                       tracked row and lets a row be routed back
                                       to its own storage bucket (existing trip
                                       PDF rows are backfilled from their
                                       'trips/' key prefix)

Revision ID: 0023_analytics_cost_and_uploads
Revises: 0022_saved_places
Create Date: 2026-09-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_analytics_cost_and_uploads"
down_revision: Union[str, None] = "0022_saved_places"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORY_VALUES = ("attachment", "travel_document", "trip_pdf")


def upgrade() -> None:
    # --- ai_usage_records.trip_id ---
    op.add_column(
        "ai_usage_records",
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_usage_records_trip_id", "ai_usage_records", "trips", ["trip_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_ai_usage_records_trip_id", "ai_usage_records", ["trip_id"])

    # --- booking_clicks ---
    op.create_table(
        "booking_clicks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trip_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("estimated_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("estimated_value_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("estimated_commission_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trip_item_id"], ["trip_items.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_booking_clicks_user_id", "booking_clicks", ["user_id"])
    op.create_index("ix_booking_clicks_trip_id", "booking_clicks", ["trip_id"])
    op.create_index("ix_booking_clicks_trip_item_id", "booking_clicks", ["trip_item_id"])
    op.create_index("ix_booking_clicks_created_at", "booking_clicks", ["created_at"])
    # Not covered by RLS: like ai_usage_records/analytics_events, this is an
    # internal analytics table with no direct per-row user-facing endpoint —
    # only admin reporting and the click-tracking write path touch it.

    # --- attachments.category ---
    category_enum = postgresql.ENUM(*_CATEGORY_VALUES, name="attachment_category_enum")
    category_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "attachments",
        sa.Column(
            "category", category_enum, nullable=False, server_default="attachment"
        ),
    )
    # Backfill: generated trip PDFs already exist as attachment rows and live
    # under the "trips/" storage prefix (user uploads are under "users/").
    op.execute("UPDATE attachments SET category = 'trip_pdf' WHERE storage_key LIKE 'trips/%'")


def downgrade() -> None:
    op.drop_column("attachments", "category")
    op.execute("DROP TYPE IF EXISTS attachment_category_enum")

    op.drop_index("ix_booking_clicks_created_at", table_name="booking_clicks")
    op.drop_index("ix_booking_clicks_trip_item_id", table_name="booking_clicks")
    op.drop_index("ix_booking_clicks_trip_id", table_name="booking_clicks")
    op.drop_index("ix_booking_clicks_user_id", table_name="booking_clicks")
    op.drop_table("booking_clicks")

    op.drop_index("ix_ai_usage_records_trip_id", table_name="ai_usage_records")
    op.drop_constraint("fk_ai_usage_records_trip_id", "ai_usage_records", type_="foreignkey")
    op.drop_column("ai_usage_records", "trip_id")
