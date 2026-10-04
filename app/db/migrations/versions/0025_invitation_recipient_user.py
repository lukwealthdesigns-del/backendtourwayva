"""@username invitations:

  * trip_invitations.recipient_user_id — set when an invitation is addressed by @username, so
    the API can show the invitee's public profile to the inviter instead of their email

Revision ID: 0025_invitation_recipient_user
Revises: 0024_payment_refunds_disputes
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_invitation_recipient_user"
down_revision: Union[str, None] = "0024_payment_refunds_disputes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trip_invitations",
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_trip_invitations_recipient_user_id_users",
        "trip_invitations",
        "users",
        ["recipient_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_trip_invitations_recipient_user_id", "trip_invitations", ["recipient_user_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_invitations_recipient_user_id", table_name="trip_invitations")
    op.drop_constraint("fk_trip_invitations_recipient_user_id_users", "trip_invitations", type_="foreignkey")
    op.drop_column("trip_invitations", "recipient_user_id")
