"""feature_flags: global admin toggles (kill switch / open to all) per feature flag

Revision ID: 0018_feature_flags
Revises: 0017_admin_rbac
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_feature_flags"
down_revision: Union[str, None] = "0017_admin_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `feature_flag_enum` already exists (feature_flag_overrides); reuse it without re-creating.
    flag_enum = postgresql.ENUM(name="feature_flag_enum", create_type=False)
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("flag", flag_enum, nullable=False),
        sa.Column("is_killed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_open_to_all", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
    )
    op.create_index("ix_feature_flags_flag", "feature_flags", ["flag"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_feature_flags_flag", table_name="feature_flags")
    op.drop_table("feature_flags")
