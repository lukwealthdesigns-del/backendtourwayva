"""
NotificationService (Master Blueprint §46-47).

`notify()` is the single entry point other services call to raise a
notification — it checks the recipient's NotificationPreference
(creating a default all-enabled row lazily if none exists) and sends
email only if enabled there, in addition to always writing the in-app
Notification row when in_app_enabled. Called from:
  - InvitationService (trip invitation received)
  - PendingChangeService (itinerary change proposed / decided)
  - CommentService (new comment, to the trip owner)
  - AdminMessagingService (admin message received)
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import NotificationType
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models.notification import Notification, NotificationPreference
from app.providers.email.factory import get_email_provider
from app.providers.email.interface import EmailProvider
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository
from app.utils.html import paragraphs_html


class NotificationService:
    def __init__(self, db: AsyncSession, email_provider: Optional[EmailProvider] = None):
        self.db = db
        self.email_provider = email_provider or get_email_provider(db)
        self.repo = NotificationRepository(db)
        self.user_repo = UserRepository(db)

    async def _get_or_create_preferences(self, user_id: uuid.UUID) -> NotificationPreference:
        prefs = await self.repo.get_preferences(user_id)
        if prefs is None:
            prefs = NotificationPreference(user_id=user_id, email_enabled=True, in_app_enabled=True)
            await self.repo.create_preferences(prefs)
        return prefs

    async def notify(
        self,
        *,
        user_id: uuid.UUID,
        notification_type: NotificationType,
        title: str,
        body: str,
        link: Optional[str] = None,
        send_email: bool = False,
    ) -> Optional[Notification]:
        prefs = await self._get_or_create_preferences(user_id)

        notification = None
        if prefs.in_app_enabled:
            notification = Notification(
                user_id=user_id, notification_type=notification_type, title=title, body=body, link=link
            )
            await self.repo.create_notification(notification)

        if send_email and prefs.email_enabled:
            user = await self.user_repo.get_by_id(user_id)
            if user is not None:
                try:
                    await self.email_provider.send_transactional_email(
                        to_email=user.email, subject=title, html_content=paragraphs_html(body)
                    )
                except Exception:  # noqa: BLE001
                    pass  # notification email is best-effort; the in-app row is the source of truth

        await self.db.commit()
        return notification

    async def list_my_notifications(self, user_id: uuid.UUID, *, unread_only: bool = False):
        return await self.repo.list_for_user(user_id, unread_only=unread_only)

    async def mark_read(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> Notification:
        from datetime import datetime, timezone

        notification = await self.repo.get_notification(notification_id)
        if notification is None:
            raise NotFoundError("Notification not found.")
        if notification.user_id != user_id:
            raise ForbiddenError("You do not have access to this notification.")

        notification.read_at = datetime.now(timezone.utc)
        await self.repo.save_notification(notification)
        await self.db.commit()
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        changed = await self.repo.mark_all_read(user_id)
        await self.db.commit()
        return changed

    async def delete(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Another user's id and an unknown id both answer 404, so ids cannot be probed."""
        if not await self.repo.delete_for_user(notification_id, user_id):
            raise NotFoundError("Notification not found.")
        await self.db.commit()

    async def clear_all(self, user_id: uuid.UUID) -> int:
        removed = await self.repo.delete_all_for_user(user_id)
        await self.db.commit()
        return removed

    async def get_preferences(self, user_id: uuid.UUID) -> NotificationPreference:
        prefs = await self._get_or_create_preferences(user_id)
        await self.db.commit()
        return prefs

    async def update_preferences(
        self, *, user_id: uuid.UUID, email_enabled: bool, in_app_enabled: bool
    ) -> NotificationPreference:
        prefs = await self._get_or_create_preferences(user_id)
        prefs.email_enabled = email_enabled
        prefs.in_app_enabled = in_app_enabled
        await self.repo.save_preferences(prefs)
        await self.db.commit()
        return prefs
