"""Structured data attached to a Companion reply:

  * messages.meta (JSONB, nullable) — `{"trip_ids": [...], "images": [...]}` so the app can show trip cards and
    photos next to a reply without guessing from its text. NULL for older messages and replies with nothing to attach.

Revision ID: 0027_message_meta
Revises: 0026_trip_member_archive
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_message_meta"
down_revision: Union[str, None] = "0026_trip_member_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "meta")
