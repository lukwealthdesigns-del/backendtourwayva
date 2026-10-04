"""
Celery application for background jobs (Master Blueprint §69):
emails, broadcasts, embeddings, file processing/OCR, image processing,
cache refresh, analytics aggregation, long AI workflows.

Only email-sending is wired to a real task in this delivery (used
implicitly via the synchronous BrevoEmailProvider call today — moving
it onto this queue is a Phase 8 hardening step). Other task types are
scaffolded as the relevant modules are built out.
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tourwayva",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    imports=("app.workers.broadcast_tasks", "app.workers.planning_tasks", "app.workers.email_tasks",
             "app.workers.attachment_tasks", "app.workers.rag_tasks",
             "app.workers.maintenance_tasks"),
)

# Additional task modules are added to `imports` above as they're
# built, e.g.:
# imports=("app.workers.broadcast_tasks", "app.workers.email_tasks", "app.workers.embedding_tasks")
