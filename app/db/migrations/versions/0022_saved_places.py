"""saved_places table (Master Blueprint §75) + RLS coverage for it

Revision ID: 0022_saved_places
Revises: 0021_row_level_security
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_saved_places"
down_revision: Union[str, None] = "0021_row_level_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORY_VALUES = (
    "attraction", "restaurant", "landmark", "museum", "park", "shopping", "nightlife", "other"
)
_SETTING = "current_setting('app.user_id', true)"
_UNSET = f"({_SETTING} = '')"
_SELF = f"({_SETTING} = user_id::text)"


def upgrade() -> None:
    category_enum = postgresql.ENUM(*_CATEGORY_VALUES, name="place_category_enum", create_type=False)
    op.create_table(
        "saved_places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_saved_places_user_id", "saved_places", ["user_id"])

    # Extends migration 0021's RLS coverage (same non-breaking pattern: falls through when
    # app.user_id is unset).
    op.execute("ALTER TABLE saved_places ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY saved_places_owner_access ON saved_places FOR ALL USING ({_UNSET} OR {_SELF}) WITH CHECK ({_UNSET} OR {_SELF})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS saved_places_owner_access ON saved_places")
    op.execute("ALTER TABLE saved_places DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_saved_places_user_id", table_name="saved_places")
    op.drop_table("saved_places")
