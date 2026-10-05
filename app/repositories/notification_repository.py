"""Repository for notifications, preferences, and email logs."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification import EmailLog, Notification, NotificationPreference


class NotificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_notification(self, notification: Notification) -> Notification:
        self.db.add(notification)
        await self.db.flush()
        return notification

    async def list_for_user(self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50) -> Sequence[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_notification(self, notification_id: uuid.UUID) -> Optional[Notification]:
        result = await self.db.execute(select(Notification).where(Notification.id == notification_id))
        return result.scalar_one_or_none()

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        """Marks every unread notification of the user as read in ONE statement. Returns how many changed."""
        result = await self.db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(timezone.utc))
        )
        return result.rowcount or 0

    async def delete_for_user(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Deletes one notification, but only if it belongs to the user (the ownership check is part of the SQL)."""
        result = await self.db.execute(
            delete(Notification).where(Notification.id == notification_id, Notification.user_id == user_id)
        )
        return bool(result.rowcount)

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(delete(Notification).where(Notification.user_id == user_id))
        return result.rowcount or 0

    async def save_notification(self, notification: Notification) -> Notification:
        await self.db.flush()
        return notification

    async def get_preferences(self, user_id: uuid.UUID) -> Optional[NotificationPreference]:
        result = await self.db.execute(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create_preferences(self, prefs: NotificationPreference) -> NotificationPreference:
        self.db.add(prefs)
        await self.db.flush()
        return prefs

    async def save_preferences(self, prefs: NotificationPreference) -> NotificationPreference:
        await self.db.flush()
        return prefs

    async def create_email_log(self, log: EmailLog) -> EmailLog:
        self.db.add(log)
        await self.db.flush()
        return log
