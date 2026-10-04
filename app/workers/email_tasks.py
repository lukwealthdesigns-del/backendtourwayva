"""Email delivery task (Master Prompt §47, §69)."""
from __future__ import annotations

import asyncio

from app.core.exceptions import ProviderUnavailableError
from app.db.session import AsyncSessionLocal
from app.providers.email.brevo_provider import BrevoEmailProvider
from app.workers.celery_app import celery_app
from app.workers.email_delivery import deliver_queued_email


@celery_app.task(
    name="email.deliver",
    acks_late=True,
    ignore_result=True,
    autoretry_for=(ProviderUnavailableError,),   # permanent rejections (PermanentEmailError) are NOT retried
    retry_backoff=5,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=6,
)
def deliver_email_task(payload_key: str) -> str:
    return asyncio.run(_deliver(payload_key))


async def _deliver(payload_key: str) -> str:
    async with AsyncSessionLocal() as db:
        try:
            return await deliver_queued_email(payload_key, BrevoEmailProvider(db=db))
        finally:
            await db.commit()       # keep the EmailLog row of a FAILED attempt too
