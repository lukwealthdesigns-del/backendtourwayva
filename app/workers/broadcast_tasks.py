"""
Broadcast background task (Master Blueprint §53).

Runs in the `worker` container (see docker-compose.yml), never inside
an API request. Resolves the target segment, sends per-recipient
(email and/or records an in-app AdminMessage), and updates the
BroadcastJob's status/recipient_count when done.

Segment resolution is deliberately straightforward — not
highly-optimized batch SQL — since it runs in the background where an
extra second or two doesn't block anyone: it fetches active users,
then checks subscription/trial status per user via
MonetizationRepository. Fine for moderate user counts; batching this
into fewer queries is a reasonable Phase 8 hardening item once real
usage data shows it's needed.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.constants import (
    AdminMessageChannel,
    AdminMessageStatus,
    BroadcastSegment,
    BroadcastStatus,
    UserStatus,
)
from app.core.logging import get_logger
from app.db.models.admin import AdminMessage, BroadcastJob
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.providers.email.brevo_provider import BrevoEmailProvider
from app.utils.html import paragraphs_html
from app.repositories.monetization_repository import MonetizationRepository
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="broadcast.send")
def send_broadcast_task(broadcast_id: str) -> None:
    """Celery entrypoint (sync) — runs the async implementation via
    asyncio.run, since this executes in the worker process's own
    event loop, separate from the API server's."""
    asyncio.run(_send_broadcast_async(uuid.UUID(broadcast_id)))


async def _resolve_segment(db, segment: BroadcastSegment) -> list[User]:
    if segment == BroadcastSegment.INACTIVE:
        result = await db.execute(select(User).where(User.status != UserStatus.ACTIVE))
        return list(result.scalars().all())

    result = await db.execute(select(User).where(User.status == UserStatus.ACTIVE))
    active_users = list(result.scalars().all())

    if segment == BroadcastSegment.ALL:
        return active_users

    repo = MonetizationRepository(db)
    now = datetime.now(timezone.utc)
    matched: list[User] = []

    for user in active_users:
        subscription = await repo.get_active_subscription(user.id)
        trial = await repo.get_user_trial(user.id)
        has_trial = trial is not None and trial.expires_at > now

        if segment == BroadcastSegment.PREMIUM and subscription is not None:
            matched.append(user)
        elif segment == BroadcastSegment.TRIAL and has_trial:
            matched.append(user)
        elif segment == BroadcastSegment.FREE and subscription is None and not has_trial:
            matched.append(user)

    return matched


async def _send_broadcast_async(broadcast_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        job = (
            await db.execute(select(BroadcastJob).where(BroadcastJob.id == broadcast_id))
        ).scalar_one_or_none()
        if job is None:
            logger.error("broadcast_job_not_found", broadcast_id=str(broadcast_id))
            return

        job.status = BroadcastStatus.PROCESSING
        await db.commit()

        try:
            recipients = await _resolve_segment(db, job.segment)
        except Exception as exc:  # noqa: BLE001
            job.status = BroadcastStatus.FAILED
            job.failure_reason = str(exc)
            await db.commit()
            logger.error("broadcast_segment_resolution_failed", error=str(exc))
            return

        email_provider = BrevoEmailProvider()   # already inside a worker: send directly, do not queue from the queue
        sent_count = 0

        for user in recipients:
            status = AdminMessageStatus.SENT
            if job.channel in (AdminMessageChannel.EMAIL, AdminMessageChannel.BOTH):
                try:
                    await email_provider.send_transactional_email(
                        to_email=user.email,
                        subject="An update from Tour-Wayva",
                        html_content=paragraphs_html(job.message),
                    )
                except Exception as exc:  # noqa: BLE001
                    status = AdminMessageStatus.FAILED
                    logger.warning("broadcast_email_failed", user_id=str(user.id), error=str(exc))

            db.add(
                AdminMessage(
                    sender_admin_id=job.sender_admin_id, recipient_user_id=user.id,
                    message=job.message, channel=job.channel, status=status,
                )
            )
            sent_count += 1

        job.status = BroadcastStatus.COMPLETED
        job.recipient_count = sent_count
        await db.commit()
        logger.info("broadcast_completed", broadcast_id=str(broadcast_id), recipient_count=sent_count)
