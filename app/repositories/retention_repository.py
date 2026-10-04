"""Bulk deletes for the retention job. One statement per table; each returns the row count."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification import EmailLog, Notification
from app.db.models.otp import OTPCode
from app.db.models.payment import PaymentWebhookEvent
from app.db.models.security import LoginAttempt
from app.db.models.analytics import ApiUsageRecord, ProviderUsageRecord
from app.db.models.provider_cache import CurrencyRateCacheEntry
from app.db.models.session import UserSession


class RetentionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _delete(self, statement) -> int:
        result = await self.db.execute(statement)
        return int(result.rowcount or 0)

    async def delete_finished_otps(self, *, created_before: datetime, now: datetime) -> int:
        """OTP rows that can no longer be used (consumed or expired) and are old."""
        return await self._delete(delete(OTPCode).where(
            OTPCode.created_at < created_before,
            or_(OTPCode.consumed_at.is_not(None), OTPCode.expires_at < now),
        ))

    async def delete_dead_sessions(self, *, before: datetime) -> int:
        """Sessions that expired or were revoked long ago (kept a while for the device list / audits)."""
        return await self._delete(delete(UserSession).where(
            or_(UserSession.expires_at < before, and_(UserSession.revoked_at.is_not(None), UserSession.revoked_at < before))
        ))

    async def delete_login_attempts(self, *, before: datetime) -> int:
        return await self._delete(delete(LoginAttempt).where(LoginAttempt.created_at < before))

    async def delete_email_logs(self, *, before: datetime) -> int:
        return await self._delete(delete(EmailLog).where(EmailLog.created_at < before))

    async def delete_read_notifications(self, *, before: datetime) -> int:
        return await self._delete(delete(Notification).where(Notification.read_at.is_not(None), Notification.created_at < before))

    async def delete_webhook_events(self, *, before: datetime) -> int:
        return await self._delete(delete(PaymentWebhookEvent).where(PaymentWebhookEvent.created_at < before))

    async def delete_provider_usage(self, *, before: datetime) -> int:
        return await self._delete(delete(ProviderUsageRecord).where(ProviderUsageRecord.created_at < before))

    async def delete_api_usage(self, *, before: datetime) -> int:
        return await self._delete(delete(ApiUsageRecord).where(ApiUsageRecord.created_at < before))

    async def delete_expired_currency_cache(self, *, before: datetime) -> int:
        """Historical rate rows only — a row still within ITS OWN `expires_at` is the current
        rate and is kept regardless of how old `created_at` is."""
        return await self._delete(delete(CurrencyRateCacheEntry).where(
            CurrencyRateCacheEntry.created_at < before, CurrencyRateCacheEntry.expires_at < func.now()
        ))
