"""places

Revision ID: 0003_places
Revises: 0002_trips_itinerary
Create Date: 2026-09-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_places"
down_revision: Union[str, None] = "0002_trips_itinerary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "attraction", "restaurant", "landmark", "museum", "park", "shopping", "nightlife", "other",
                name="place_category_enum",
            ),
            nullable=False,
        ),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_places_name", "places", ["name"])
    op.create_index("ix_places_city", "places", ["city"])


def downgrade() -> None:
    op.drop_index("ix_places_city", table_name="places")
    op.drop_index("ix_places_name", table_name="places")
    op.drop_table("places")
    sa.Enum(name="place_category_enum").drop(op.get_bind(), checkfirst=True)
