"""Sets the per-request session variable the RLS policies (migration 0021) key on.

Best-effort and silent when RLS_ENFORCE is off (the default) or the database predates the
migration — nothing here changes behaviour until an operator turns RLS_ENFORCE on. `SET LOCAL`
scopes the setting to the current transaction only, so it is automatically cleared when the
request's session closes; there is nothing to "unset" between requests.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def set_rls_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    if not settings.RLS_ENFORCE:
        return
    try:
        await db.execute(text("SET LOCAL app.user_id = :uid"), {"uid": str(user_id)})
    except Exception as exc:  # noqa: BLE001 - never fail a request over defense-in-depth plumbing
        logger.warning("rls_session_setup_failed", error=str(exc))
