"""
Data retention (Master Prompt §9 "apply appropriate retention policies", §77).

Operational records that hold personal data (IP addresses in login attempts, email addresses in
email logs, one-time codes, device sessions, notifications) are deleted once they have served their
purpose. Windows come from settings. NEVER touched: the admin audit log (append-only by design),
payments and subscription history (accounting), security events.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.retention_repository import RetentionRepository

logger = get_logger(__name__)


class RetentionService:
    def __init__(self, db: AsyncSession, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db = db
        self.repo = RetentionRepository(db)
        self._now = now

    async def run(self) -> dict[str, int]:
        now = self._now()
        counts = {
            "otp_codes": await self.repo.delete_finished_otps(
                created_before=now - timedelta(hours=settings.OTP_RETENTION_HOURS), now=now),
            "user_sessions": await self.repo.delete_dead_sessions(before=now - timedelta(days=settings.SESSION_RETENTION_DAYS)),
            "login_attempts": await self.repo.delete_login_attempts(before=now - timedelta(days=settings.SECURITY_LOG_RETENTION_DAYS)),
            "email_logs": await self.repo.delete_email_logs(before=now - timedelta(days=settings.EMAIL_LOG_RETENTION_DAYS)),
            "read_notifications": await self.repo.delete_read_notifications(
                before=now - timedelta(days=settings.NOTIFICATION_RETENTION_DAYS)),
            "webhook_events": await self.repo.delete_webhook_events(before=now - timedelta(days=settings.WEBHOOK_EVENT_RETENTION_DAYS)),
            "provider_usage": await self.repo.delete_provider_usage(before=now - timedelta(days=settings.PROVIDER_USAGE_RETENTION_DAYS)),
            "api_usage": await self.repo.delete_api_usage(before=now - timedelta(days=settings.API_USAGE_RETENTION_DAYS)),
            "currency_cache": await self.repo.delete_expired_currency_cache(before=now - timedelta(days=settings.CURRENCY_CACHE_RETENTION_DAYS)),
        }
        await self.db.commit()
        if any(counts.values()):
            logger.info("retention_run", **counts)
        return counts
