"""
QueuedEmailProvider — hands every email to a Celery worker (Master Prompt §47:
"Email sending should happen asynchronously through workers").

It implements the same EmailProvider interface, so EVERY caller (OTPs,
invitations, admin messages, notifications) becomes asynchronous without
changing a line: the request returns as soon as the job is queued instead of
waiting for Brevo, and delivery is retried with backoff by the worker.

Secrets stay out of the broker's task arguments: an OTP email carries the code,
and Celery may log task arguments on failure. So the message is parked in Redis
under a random one-time key with a short TTL, and the task carries ONLY that key.

If the broker cannot be reached the email is sent directly (EMAIL_QUEUE_FALLBACK_INLINE)
rather than silently lost.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Optional

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.email.interface import EmailProvider
from app.services.cache_service import CacheService

logger = get_logger(__name__)

PAYLOAD_KEY_PREFIX = "emailq:"
TASK_NAME = "email.deliver"


async def _celery_enqueue(task_name: str, kwargs: dict[str, Any]) -> None:
    from app.workers.celery_app import celery_app

    # send_task is a short blocking network call to the broker; keep it off the event loop.
    await asyncio.to_thread(celery_app.send_task, task_name, kwargs=kwargs)


class QueuedEmailProvider(EmailProvider):
    def __init__(
        self,
        direct: EmailProvider,
        *,
        enqueue: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
    ):
        self._direct = direct
        self._enqueue = enqueue or _celery_enqueue

    async def send_otp_email(self, *, to_email: str, first_name: str, otp_code: str, purpose_label: str) -> bool:
        kwargs = {"to_email": to_email, "first_name": first_name, "otp_code": otp_code, "purpose_label": purpose_label}
        return await self._dispatch("otp", kwargs, lambda: self._direct.send_otp_email(**kwargs))

    async def send_transactional_email(
        self, *, to_email: str, subject: str, html_content: str, category: str = "transactional"
    ) -> bool:
        kwargs = {"to_email": to_email, "subject": subject, "html_content": html_content, "category": category}
        return await self._dispatch("transactional", kwargs, lambda: self._direct.send_transactional_email(**kwargs))

    async def _dispatch(self, kind: str, kwargs: dict[str, Any], send_directly: Callable[[], Awaitable[bool]]) -> bool:
        key = uuid.uuid4().hex
        try:
            parked = await CacheService.set_if_absent(
                PAYLOAD_KEY_PREFIX + key, json.dumps({"kind": kind, "kwargs": kwargs}), settings.EMAIL_PAYLOAD_TTL_SECONDS
            )
            if parked is not True:
                raise ConnectionError("could not park the email payload")
            await self._enqueue(TASK_NAME, {"payload_key": key})
            return True                      # queued; delivery (and retries) happen in the worker
        except Exception as exc:  # noqa: BLE001
            logger.error("email_enqueue_failed", kind=kind, error=str(exc))
            await CacheService.delete(PAYLOAD_KEY_PREFIX + key)
            if settings.EMAIL_QUEUE_FALLBACK_INLINE:
                return await send_directly()
            raise ProviderUnavailableError("Could not queue the email right now. Please try again.") from exc
