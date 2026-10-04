"""Planning: trip overview, item provenance columns, trip_preferences

Revision ID: 0015_planning
Revises: 0014_payments_paystack
Create Date: 2026-09-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_planning"
down_revision: Union[str, None] = "0014_payments_paystack"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("overview", sa.Text(), nullable=True))

    op.add_column("trip_items", sa.Column("source", sa.String(20), nullable=True))
    op.add_column("trip_items", sa.Column("external_id", sa.String(100), nullable=True))
    op.add_column("trip_items", sa.Column("booking_link", sa.String(500), nullable=True))
    op.add_column("trip_items", sa.Column("image_url", sa.String(500), nullable=True))

    op.create_table(
        "trip_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("travel_style", sa.String(50), nullable=True),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("pace", sa.String(20), nullable=True),
        sa.Column("walking_preference", sa.String(20), nullable=True),
        sa.Column("hotel_preference", sa.String(50), nullable=True),
        sa.Column("transport_preference", sa.String(50), nullable=True),
        sa.Column("food_preferences", sa.JSON(), nullable=False),
        sa.Column("must_see", sa.JSON(), nullable=False),
        sa.Column("avoid", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_trip_preferences_trip_id", "trip_preferences", ["trip_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_trip_preferences_trip_id", table_name="trip_preferences")
    op.drop_table("trip_preferences")
    op.drop_column("trip_items", "image_url")
    op.drop_column("trip_items", "booking_link")
    op.drop_column("trip_items", "external_id")
    op.drop_column("trip_items", "source")
    op.drop_column("trips", "overview")
