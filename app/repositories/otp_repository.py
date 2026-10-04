"""OTP code repository."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import OTPPurpose
from app.db.models.otp import OTPCode


class OTPRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, otp: OTPCode) -> OTPCode:
        self.db.add(otp)
        await self.db.flush()
        return otp

    async def get_latest_active(
        self, user_id: uuid.UUID, purpose: OTPPurpose
    ) -> OTPCode | None:
        """Latest, unconsumed, unexpired OTP for this user/purpose."""
        result = await self.db.execute(
            select(OTPCode)
            .where(
                OTPCode.user_id == user_id,
                OTPCode.purpose == purpose,
                OTPCode.consumed_at.is_(None),
            )
            .order_by(OTPCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_any(self, user_id: uuid.UUID, purpose: OTPPurpose) -> OTPCode | None:
        """Most recent OTP for this user/purpose regardless of state —
        used to enforce the resend cooldown."""
        result = await self.db.execute(
            select(OTPCode)
            .where(OTPCode.user_id == user_id, OTPCode.purpose == purpose)
            .order_by(OTPCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def invalidate_active(self, user_id: uuid.UUID, purpose: OTPPurpose) -> None:
        """Mark any existing active OTPs for this user/purpose as consumed
        (used) so only the newest one is valid — prevents stale codes
        lingering after a resend."""
        result = await self.db.execute(
            select(OTPCode).where(
                OTPCode.user_id == user_id,
                OTPCode.purpose == purpose,
                OTPCode.consumed_at.is_(None),
            )
        )
        now = datetime.now(timezone.utc)
        for otp in result.scalars().all():
            otp.consumed_at = now
        await self.db.flush()

    async def save(self, otp: OTPCode) -> OTPCode:
        await self.db.flush()
        return otp
