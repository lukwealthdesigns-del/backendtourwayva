"""Paystack payments: payments, subscription_events, payment_webhook_events, plan/subscription columns

Revision ID: 0014_payments_paystack
Revises: 0013_sessions_preferences
Create Date: 2026-09-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_payments_paystack"
down_revision: Union[str, None] = "0013_sessions_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- plans / subscriptions ---
    op.add_column("plans", sa.Column("paystack_plan_code", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_plans_paystack_plan_code", "plans", ["paystack_plan_code"])

    op.add_column("subscriptions", sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("subscriptions", sa.Column("paystack_customer_code", sa.String(64), nullable=True))
    op.add_column("subscriptions", sa.Column("paystack_subscription_code", sa.String(64), nullable=True))
    op.add_column("subscriptions", sa.Column("paystack_email_token", sa.String(100), nullable=True))
    op.create_index("ix_subscriptions_paystack_customer_code", "subscriptions", ["paystack_customer_code"])
    op.create_index("ix_subscriptions_paystack_subscription_code", "subscriptions", ["paystack_subscription_code"])

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="paystack"),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "success", "failed", "abandoned", name="payment_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("is_renewal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_response", sa.String(255), nullable=True),
        sa.Column("provider_customer_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index("ix_payments_reference", "payments", ["reference"], unique=True)
    op.create_index("ix_payments_status", "payments", ["status"])

    # --- subscription_events ---
    op.create_table(
        "subscription_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("provider_reference", sa.String(100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_subscription_events_user_id", "subscription_events", ["user_id"])
    op.create_index("ix_subscription_events_subscription_id", "subscription_events", ["subscription_id"])
    op.create_index("ix_subscription_events_event_type", "subscription_events", ["event_type"])
    op.create_index("ix_subscription_events_provider_reference", "subscription_events", ["provider_reference"])

    # --- payment_webhook_events ---
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_webhook_events_event_key", "payment_webhook_events", ["event_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_payment_webhook_events_event_key", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")

    op.drop_index("ix_subscription_events_provider_reference", table_name="subscription_events")
    op.drop_index("ix_subscription_events_event_type", table_name="subscription_events")
    op.drop_index("ix_subscription_events_subscription_id", table_name="subscription_events")
    op.drop_index("ix_subscription_events_user_id", table_name="subscription_events")
    op.drop_table("subscription_events")

    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_reference", table_name="payments")
    op.drop_index("ix_payments_subscription_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    sa.Enum(name="payment_status_enum").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_subscriptions_paystack_subscription_code", table_name="subscriptions")
    op.drop_index("ix_subscriptions_paystack_customer_code", table_name="subscriptions")
    op.drop_column("subscriptions", "paystack_email_token")
    op.drop_column("subscriptions", "paystack_subscription_code")
    op.drop_column("subscriptions", "paystack_customer_code")
    op.drop_column("subscriptions", "auto_renew")

    op.drop_constraint("uq_plans_paystack_plan_code", "plans", type_="unique")
    op.drop_column("plans", "paystack_plan_code")
