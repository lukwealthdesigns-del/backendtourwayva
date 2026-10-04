"""Attachment extraction worker task (Master Prompt §42, §69)."""
from __future__ import annotations

import asyncio
import uuid

from app.core.exceptions import ProviderUnavailableError
from app.db.session import AsyncSessionLocal
from app.modules.attachments.processing import mark_failed, process_attachment
from app.workers.celery_app import celery_app


@celery_app.task(
    name="attachments.process",
    bind=True,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(ProviderUnavailableError,),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    soft_time_limit=180,
    time_limit=240,
)
def process_attachment_task(self, attachment_id: str) -> str:
    try:
        return asyncio.run(_run(attachment_id))
    except ProviderUnavailableError:
        if self.request.retries >= self.max_retries:
            asyncio.run(_give_up(attachment_id))     # do not leave it "pending" forever
        raise


async def _run(attachment_id: str) -> str:
    async with AsyncSessionLocal() as db:
        return await process_attachment(db, uuid.UUID(attachment_id))


async def _give_up(attachment_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await mark_failed(db, uuid.UUID(attachment_id))
