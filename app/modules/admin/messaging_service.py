"""AdminMessagingService (Master Blueprint §52)."""
from __future__ import annotations

from typing import Optional

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AdminMessageChannel, AdminMessageStatus, AuditResult
from app.core.exceptions import NotFoundError
from app.db.models.admin import AdminMessage
from app.modules.admin.admin_service import AdminService
from app.providers.email.factory import get_email_provider
from app.utils.html import paragraphs_html
from app.providers.email.interface import EmailProvider
from app.repositories.admin_repository import AdminRepository
from app.repositories.user_repository import UserRepository


class AdminMessagingService:
    def __init__(self, db: AsyncSession, email_provider: Optional[EmailProvider] = None):
        self.db = db
        self.repo = AdminRepository(db)
        self.user_repo = UserRepository(db)
        self.admin_service = AdminService(db)
        self.email_provider = email_provider or get_email_provider(db)

    async def send_message(
        self, *, actor_id: uuid.UUID, recipient_user_id: uuid.UUID, message: str, channel: AdminMessageChannel
    ) -> AdminMessage:
        await self.admin_service.require_permission(user_id=actor_id, permission="messaging:send")

        recipient = await self.user_repo.get_by_id(recipient_user_id)
        if recipient is None:
            raise NotFoundError("Recipient not found.")

        status = AdminMessageStatus.SENT
        if channel in (AdminMessageChannel.EMAIL, AdminMessageChannel.BOTH):
            try:
                await self.email_provider.send_transactional_email(
                    to_email=recipient.email,
                    subject="Message from the Tour-Wayva team",
                    html_content=paragraphs_html(message),
                )
            except Exception:  # noqa: BLE001
                status = AdminMessageStatus.FAILED

        record = AdminMessage(
            sender_admin_id=actor_id, recipient_user_id=recipient_user_id,
            message=message, channel=channel, status=status,
        )
        await self.repo.create_message(record)

        try:
            from app.core.constants import NotificationType
            from app.modules.notifications.service import NotificationService

            await NotificationService(self.db).notify(
                user_id=recipient_user_id,
                notification_type=NotificationType.ADMIN_MESSAGE,
                title="New message from the Tour-Wayva team",
                body=message,
            )
        except Exception:  # noqa: BLE001
            pass

        await self.admin_service.log(
            admin_user_id=actor_id, action="message.send", target_type="user",
            target_id=str(recipient_user_id), result=AuditResult.SUCCESS,
            metadata={"channel": channel.value, "delivery_status": status.value},
        )
        await self.db.commit()
        return record

    async def list_my_messages(self, user_id: uuid.UUID):
        """Any authenticated user — this reads THEIR OWN inbox, no
        admin permission required."""
        return await self.repo.list_messages_for_recipient(user_id)
