"""pending_change_action_enum: add 'revise_trip' (conversational itinerary revisions)

Revision ID: 0016_pending_change_revise
Revises: 0015_planning
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016_pending_change_revise"
down_revision: Union[str, None] = "0015_planning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction block on older PostgreSQL.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE pending_change_action_enum ADD VALUE IF NOT EXISTS 'revise_trip'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single value from an enum type; 'revise_trip' is left in place.
    pass
