"""Background work: lifecycle notification markers, knowledge-document ingestion status, daily_metrics

Revision ID: 0019_workers
Revises: 0018_feature_flags
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_workers"
down_revision: Union[str, None] = "0018_feature_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_trials", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_trials", sa.Column("expired_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("expired_notified_at", sa.DateTime(timezone=True), nullable=True))
    # Notifications about EXISTING expired trials/subscriptions would be noise: mark them handled.
    op.execute("UPDATE user_trials SET reminder_sent_at = now(), expired_notified_at = now() WHERE expires_at < now()")
    op.execute("UPDATE subscriptions SET reminder_sent_at = now(), expired_notified_at = now() WHERE current_period_end < now()")

    op.add_column("knowledge_documents", sa.Column("status", sa.String(20), nullable=False, server_default="ready"))
    op.add_column("knowledge_documents", sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("knowledge_documents", sa.Column("error", sa.String(255), nullable=True))
    op.execute(
        "UPDATE knowledge_documents d SET chunk_count = "
        "(SELECT count(*) FROM knowledge_chunks c WHERE c.document_id = d.id)"
    )

    op.create_table(
        "daily_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("metric_key", sa.String(80), nullable=False),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("metric_date", "metric_key", name="uq_daily_metrics_date_key"),
    )
    op.create_index("ix_daily_metrics_metric_date", "daily_metrics", ["metric_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_metrics_metric_date", table_name="daily_metrics")
    op.drop_table("daily_metrics")
    op.drop_column("knowledge_documents", "error")
    op.drop_column("knowledge_documents", "chunk_count")
    op.drop_column("knowledge_documents", "status")
    op.drop_column("subscriptions", "expired_notified_at")
    op.drop_column("subscriptions", "reminder_sent_at")
    op.drop_column("user_trials", "expired_notified_at")
    op.drop_column("user_trials", "reminder_sent_at")
