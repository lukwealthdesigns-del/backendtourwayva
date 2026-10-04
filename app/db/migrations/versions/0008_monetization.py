"""plans, subscriptions, trial_config, user_trials, feature_flag_overrides

Revision ID: 0008_monetization
Revises: 0007_collaboration
Create Date: 2026-09-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_monetization"
down_revision: Union[str, None] = "0007_collaboration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("price_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "billing_interval",
            sa.Enum("free", "monthly", "yearly", name="billing_interval_enum"),
            nullable=False,
            server_default="free",
        ),
        sa.Column("included_feature_flags", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_plans_slug"),
    )
    op.create_index("ix_plans_slug", "plans", ["slug"])

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "cancelled", "expired", name="subscription_status_enum"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    op.create_table(
        "trial_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("included_feature_flags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "user_trials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("included_feature_flags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_trials_user_id"),
    )
    op.create_index("ix_user_trials_user_id", "user_trials", ["user_id"])

    op.create_table(
        "feature_flag_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "flag",
            sa.Enum(
                "DISCOVER", "PLANNER", "COMPANION", "VOICE", "ATTACHMENTS", "MEMORY", "HOTELS", "FLIGHTS",
                "ACTIVITIES", "WEATHER", "LIVE_TRAVEL", "PDF_EXPORT", "COLLABORATION", "PREMIUM_AI",
                name="feature_flag_enum",
            ),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "flag", name="uq_feature_flag_overrides_user_flag"),
    )
    op.create_index("ix_feature_flag_overrides_user_id", "feature_flag_overrides", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_feature_flag_overrides_user_id", table_name="feature_flag_overrides")
    op.drop_table("feature_flag_overrides")

    op.drop_index("ix_user_trials_user_id", table_name="user_trials")
    op.drop_table("user_trials")

    op.drop_table("trial_config")

    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_plans_slug", table_name="plans")
    op.drop_table("plans")

    sa.Enum(name="feature_flag_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="subscription_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="billing_interval_enum").drop(op.get_bind(), checkfirst=True)
