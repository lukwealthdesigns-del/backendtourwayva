"""UserManagementService (Master Blueprint §51)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AuditResult, UserStatus
from app.core.exceptions import NotFoundError
from app.db.models.user import User
from app.modules.admin.admin_service import AdminService
from app.repositories.monetization_repository import MonetizationRepository
from app.repositories.user_repository import UserRepository


class UserManagementService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.monetization_repo = MonetizationRepository(db)
        self.admin_service = AdminService(db)

    async def search_users(
        self, *, actor_id: uuid.UUID, query: Optional[str], status: Optional[str], limit: int, offset: int
    ) -> tuple[list[User], int]:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:view")
        users = await self.user_repo.search_users(query=query, status=status, limit=limit, offset=offset)
        total = await self.user_repo.count_users(query=query, status=status)
        return list(users), total

    async def get_user_detail(self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID) -> dict:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:view")

        user = await self.user_repo.get_by_id(target_user_id)
        if user is None:
            raise NotFoundError("User not found.")

        subscription = await self.monetization_repo.get_active_subscription(target_user_id)
        trial = await self.monetization_repo.get_user_trial(target_user_id)
        has_active_trial = trial is not None and trial.expires_at > datetime.now(timezone.utc)

        return {
            "user": user,
            "has_active_subscription": subscription is not None,
            "has_active_trial": has_active_trial,
        }

    async def block_user(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, reason: str, ip_address: Optional[str] = None
    ) -> User:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:block")

        user = await self._get_target(target_user_id)
        user.status = UserStatus.SUSPENDED
        user.is_active = False
        await self.user_repo.save(user)

        await self.admin_service.log(
            admin_user_id=actor_id, action="user.block", target_type="user", target_id=str(target_user_id),
            result=AuditResult.SUCCESS, reason=reason, ip_address=ip_address,
        )
        await self.db.commit()
        return user

    async def unblock_user(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, ip_address: Optional[str] = None
    ) -> User:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:block")

        user = await self._get_target(target_user_id)
        user.status = UserStatus.ACTIVE
        user.is_active = True
        await self.user_repo.save(user)

        await self.admin_service.log(
            admin_user_id=actor_id, action="user.unblock", target_type="user", target_id=str(target_user_id),
            result=AuditResult.SUCCESS, ip_address=ip_address,
        )
        await self.db.commit()
        return user

    async def delete_user(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, reason: str, ip_address: Optional[str] = None
    ) -> User:
        """Soft-delete only (Blueprint §77 covers the full deletion
        workflow — anonymization, private-data removal — which stays
        scaffolded; this flips status/is_active, the same reversible
        state change block_user makes, just with a different status)."""
        await self.admin_service.require_permission(user_id=actor_id, permission="users:delete")

        user = await self._get_target(target_user_id)
        user.status = UserStatus.DELETED
        user.is_active = False
        await self.user_repo.save(user)

        await self.admin_service.log(
            admin_user_id=actor_id, action="user.delete", target_type="user", target_id=str(target_user_id),
            result=AuditResult.SUCCESS, reason=reason, ip_address=ip_address,
        )
        await self.db.commit()
        return user

    async def revoke_sessions(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, ip_address: Optional[str] = None
    ) -> User:
        await self.admin_service.require_permission(user_id=actor_id, permission="users:manage_sessions")

        user = await self._get_target(target_user_id)
        user.sessions_invalidated_at = datetime.now(timezone.utc)
        await self.user_repo.save(user)

        await self.admin_service.log(
            admin_user_id=actor_id, action="user.revoke_sessions", target_type="user",
            target_id=str(target_user_id), result=AuditResult.SUCCESS, ip_address=ip_address,
        )
        await self.db.commit()
        return user

    async def _get_target(self, target_user_id: uuid.UUID) -> User:
        user = await self.user_repo.get_by_id(target_user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user
