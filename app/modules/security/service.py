"""
SecurityService (Master Blueprint §62-64, §89).

Persists what app/core/rate_limit.py enforces in real time via Redis.
IP blocking here is admin-managed and permanent-by-default (or with
an expiry); it's a stronger, explicit measure than the automatic
failed-login lockout, for cases like known-abusive IPs an admin wants
to shut out entirely.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AuditResult, SecurityEventSeverity
from app.core.exceptions import NotFoundError
from app.db.models.security import BlockedIP, LoginAttempt, SecurityEvent
from app.modules.admin.admin_service import AdminService
from app.repositories.security_repository import SecurityRepository


class SecurityService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SecurityRepository(db)
        self.admin_service = AdminService(db)

    # --- Login attempt tracking (called from AuthService, no admin permission needed) ---

    async def record_login_attempt(
        self, *, email: str, ip_address: Optional[str], success: bool, user_agent: Optional[str] = None
    ) -> LoginAttempt:
        attempt = LoginAttempt(email=email.lower(), ip_address=ip_address, success=success, user_agent=user_agent)
        await self.repo.record_login_attempt(attempt)
        await self.db.commit()
        return attempt

    # --- IP blocking (admin) ---

    async def is_ip_blocked(self, ip_address: Optional[str]) -> bool:
        if not ip_address:
            return False
        return await self.repo.get_blocked_ip(ip_address) is not None

    async def block_ip(
        self, *, actor_id: uuid.UUID, ip_address: str, reason: str, duration_hours: Optional[int] = None
    ) -> BlockedIP:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:block")

        expires_at = None
        if duration_hours is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

        blocked = BlockedIP(ip_address=ip_address, reason=reason, blocked_by=actor_id, expires_at=expires_at)
        await self.repo.create_blocked_ip(blocked)

        await self.admin_service.log(
            admin_user_id=actor_id, action="security.block_ip", target_type="ip_address",
            target_id=ip_address, result=AuditResult.SUCCESS, reason=reason,
        )
        await self.db.commit()
        return blocked

    async def unblock_ip(self, *, actor_id: uuid.UUID, ip_address: str) -> None:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:block")

        blocked = await self.repo.get_blocked_ip(ip_address)
        if blocked is None:
            raise NotFoundError("This IP address is not currently blocked.")

        await self.repo.delete_blocked_ip(blocked)
        await self.admin_service.log(
            admin_user_id=actor_id, action="security.unblock_ip", target_type="ip_address",
            target_id=ip_address, result=AuditResult.SUCCESS,
        )
        await self.db.commit()

    async def list_blocked_ips(self, actor_id: uuid.UUID):
        await self.admin_service.require_permission(user_id=actor_id, permission="users:block")
        return await self.repo.list_blocked_ips()

    # --- Security events ---

    async def record_event(
        self,
        *,
        user_id: Optional[uuid.UUID],
        event_type: str,
        severity: SecurityEventSeverity,
        ip_address: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SecurityEvent:
        event = SecurityEvent(
            user_id=user_id, event_type=event_type, severity=severity,
            ip_address=ip_address, extra_metadata=metadata,
        )
        result = await self.repo.create_security_event(event)
        await self.db.commit()
        return result

    async def list_events(self, *, actor_id: uuid.UUID, limit: int = 50, offset: int = 0):
        await self.admin_service.require_permission(user_id=actor_id, permission="audit:view")
        return await self.repo.list_security_events(limit=limit, offset=offset)
