"""Delivery of a queued email (called by the `email.deliver` Celery task).

The payload was parked in Redis by QueuedEmailProvider under a one-time key. It is
read here, sent through Brevo, and only DELETED after a successful send — so a retry
after a transient failure still finds it (the parked copy expires on its own).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from app.core.logging import get_logger
from app.providers.email.interface import EmailProvider
from app.providers.email.queued_provider import PAYLOAD_KEY_PREFIX
from app.services.cache_service import CacheService

logger = get_logger(__name__)


async def deliver_queued_email(payload_key: str, provider: EmailProvider) -> str:
    """Returns "sent" or "expired" (payload no longer parked). Transient provider
    errors propagate so the task retries; permanent ones propagate so it does not."""
    raw = await CacheService.get_raw(PAYLOAD_KEY_PREFIX + payload_key)
    if raw is None:
        logger.warning("queued_email_payload_missing", key=payload_key)
        return "expired"

    payload: dict[str, Any] = json.loads(raw)
    kwargs = payload["kwargs"]
    if payload["kind"] == "otp":
        await provider.send_otp_email(**kwargs)
    else:
        await provider.send_transactional_email(**kwargs)

    await CacheService.delete(PAYLOAD_KEY_PREFIX + payload_key)
    return "sent"
