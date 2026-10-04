"""UserSession repository — the only place that queries `user_sessions`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session import UserSession


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, session: UserSession) -> UserSession:
        self.db.add(session)
        await self.db.flush()
        return session

    async def save(self, session: UserSession) -> UserSession:
        await self.db.flush()
        return session

    async def get_by_any_hash(self, jti_hash: str) -> Optional[UserSession]:
        """Session whose CURRENT or PREVIOUS refresh token id matches —
        a match on the previous one means a rotated token is being
        presented again."""
        result = await self.db.execute(
            select(UserSession).where(
                or_(UserSession.refresh_jti_hash == jti_hash, UserSession.previous_jti_hash == jti_hash)
            )
        )
        return result.scalar_one_or_none()

    async def get_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> Optional[UserSession]:
        result = await self.db.execute(
            select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[UserSession]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.last_used_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_all_for_user(self, user_id: uuid.UUID, reason: str) -> None:
        await self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
        )

    async def enforce_session_cap(self, user_id: uuid.UUID, max_active: int) -> None:
        """Keep at most `max_active` live sessions; the least recently
        used ones beyond the cap are revoked."""
        active = await self.list_active_for_user(user_id)  # newest-used first
        now = datetime.now(timezone.utc)
        for stale in active[max_active:]:
            stale.revoked_at = now
            stale.revoked_reason = "session_limit"
        await self.db.flush()

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        from sqlalchemy import delete

        await self.db.execute(delete(UserSession).where(UserSession.user_id == user_id))
