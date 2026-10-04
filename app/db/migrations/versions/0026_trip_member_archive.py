"""Per-member trip archiving:

  * trip_members.archived_at — set when a member archives a trip for themselves (NULL = not archived)

Revision ID: 0026_trip_member_archive
Revises: 0025_invitation_recipient_user
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_trip_member_archive"
down_revision: Union[str, None] = "0025_invitation_recipient_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trip_members", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("trip_members", "archived_at")
