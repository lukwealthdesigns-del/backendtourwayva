"""Repository for login attempts, blocked IPs, and security events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.security import BlockedIP, LoginAttempt, SecurityEvent


class SecurityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_login_attempt(self, attempt: LoginAttempt) -> LoginAttempt:
        self.db.add(attempt)
        await self.db.flush()
        return attempt

    async def list_recent_login_attempts(self, *, email: str, limit: int = 20) -> Sequence[LoginAttempt]:
        result = await self.db.execute(
            select(LoginAttempt).where(LoginAttempt.email == email).order_by(LoginAttempt.created_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def get_blocked_ip(self, ip_address: str) -> Optional[BlockedIP]:
        result = await self.db.execute(select(BlockedIP).where(BlockedIP.ip_address == ip_address))
        blocked = result.scalar_one_or_none()
        if blocked is None:
            return None
        if blocked.expires_at is not None and blocked.expires_at < datetime.now(timezone.utc):
            return None
        return blocked

    async def create_blocked_ip(self, blocked: BlockedIP) -> BlockedIP:
        self.db.add(blocked)
        await self.db.flush()
        return blocked

    async def list_blocked_ips(self) -> Sequence[BlockedIP]:
        result = await self.db.execute(select(BlockedIP).order_by(BlockedIP.created_at.desc()))
        return result.scalars().all()

    async def delete_blocked_ip(self, blocked: BlockedIP) -> None:
        await self.db.delete(blocked)
        await self.db.flush()

    async def create_security_event(self, event: SecurityEvent) -> SecurityEvent:
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_security_events(self, *, limit: int = 50, offset: int = 0) -> Sequence[SecurityEvent]:
        result = await self.db.execute(
            select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).offset(offset).limit(limit)
        )
        return result.scalars().all()
