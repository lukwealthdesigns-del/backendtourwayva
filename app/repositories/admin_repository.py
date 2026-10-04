"""
Repository for admin capabilities, audit logging, messaging, and
broadcasts.

The audit log methods here are deliberately limited to `create` and
`list` — there is no update/delete method, which is the concrete
mechanism behind "append-only, protected from ordinary modification"
(Blueprint §56) in a codebase with no separate database-permission
layer configured. Nothing in this codebase can edit or remove an
audit log entry once written.
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.admin import AdminAuditLog, AdminMessage, AdminUser, BroadcastJob


class AdminRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Admin users ---
    async def create_admin(self, admin: AdminUser) -> AdminUser:
        self.db.add(admin)
        await self.db.flush()
        return admin

    async def get_admin_by_user_id(self, user_id: uuid.UUID) -> Optional[AdminUser]:
        result = await self.db.execute(select(AdminUser).where(AdminUser.user_id == user_id))
        return result.scalar_one_or_none()

    async def list_admins(self) -> Sequence[AdminUser]:
        result = await self.db.execute(select(AdminUser).order_by(AdminUser.created_at))
        return result.scalars().all()

    async def save_admin(self, admin: AdminUser) -> AdminUser:
        await self.db.flush()
        return admin

    # --- Audit log (append-only — create + list only, on purpose) ---
    async def create_audit_log(self, entry: AdminAuditLog) -> AdminAuditLog:
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def list_audit_logs(self, *, limit: int = 50, offset: int = 0) -> Sequence[AdminAuditLog]:
        result = await self.db.execute(
            select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).offset(offset).limit(limit)
        )
        return result.scalars().all()

    # --- Admin messages ---
    async def create_message(self, message: AdminMessage) -> AdminMessage:
        self.db.add(message)
        await self.db.flush()
        return message

    async def list_messages_for_recipient(self, recipient_user_id: uuid.UUID) -> Sequence[AdminMessage]:
        result = await self.db.execute(
            select(AdminMessage)
            .where(AdminMessage.recipient_user_id == recipient_user_id)
            .order_by(AdminMessage.created_at.desc())
        )
        return result.scalars().all()

    # --- Broadcasts ---
    async def create_broadcast(self, job: BroadcastJob) -> BroadcastJob:
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_broadcast(self, broadcast_id: uuid.UUID) -> Optional[BroadcastJob]:
        result = await self.db.execute(select(BroadcastJob).where(BroadcastJob.id == broadcast_id))
        return result.scalar_one_or_none()

    async def save_broadcast(self, job: BroadcastJob) -> BroadcastJob:
        await self.db.flush()
        return job
