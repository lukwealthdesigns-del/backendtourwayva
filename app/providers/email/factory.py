"""Email provider factory — the one place that decides which concrete
EmailProvider implementation services receive by default.

  background mode (staging/production): QueuedEmailProvider -> Celery worker -> Brevo,
                                        with retries; the request never waits on Brevo
  inline mode (development/tests):      BrevoEmailProvider directly

Services accept an `email_provider` argument and fall back to this factory, so
tests inject MockEmailProvider explicitly and production code never instantiates
a vendor class inline. Code that already runs INSIDE a worker (broadcasts,
scheduled reminders) should pass `direct_email_provider()` — no need to queue
from the queue.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.providers.email.brevo_provider import BrevoEmailProvider
from app.providers.email.interface import EmailProvider
from app.providers.email.queued_provider import QueuedEmailProvider


def direct_email_provider(db: Optional[AsyncSession] = None) -> EmailProvider:
    return BrevoEmailProvider(db=db)


def get_email_provider(db: Optional[AsyncSession] = None) -> EmailProvider:
    direct = direct_email_provider(db)
    if settings.tasks_execution == "background":
        return QueuedEmailProvider(direct)
    return direct
