"""User repository — the only place that runs SQL queries against the
users table. Services depend on this, never on the ORM session directly
for user lookups (repository pattern, per Blueprint §109)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AuthProvider
from app.db.models.user import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, user_ids) -> list[User]:
        ids = list({uid for uid in user_ids})
        if not ids:
            return []
        result = await self.db.execute(select(User).where(User.id.in_(ids)))
        return list(result.scalars().all())

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.username == username.lower()))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone_e164: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.phone_number_e164 == phone_e164))
        return result.scalar_one_or_none()

    async def get_by_provider_subject(self, provider: AuthProvider, subject_id: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.auth_provider == provider, User.provider_subject_id == subject_id)
        )
        return result.scalar_one_or_none()

    async def username_taken_by_other(self, username: str, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(User.id).where(User.username == username.lower(), User.id != user_id)
        )
        return result.first() is not None

    async def email_exists(self, email: str) -> bool:
        return (await self.get_by_email(email)) is not None

    async def username_exists(self, username: str) -> bool:
        return (await self.get_by_username(username)) is not None

    async def phone_exists(self, phone_e164: str) -> bool:
        return (await self.get_by_phone(phone_e164)) is not None

    async def create(self, user: User) -> User:
        self.db.add(user)
        await self.db.flush()
        return user

    async def save(self, user: User) -> User:
        await self.db.flush()
        return user

    async def search_users(
        self, *, query: str | None = None, status: str | None = None, limit: int = 20, offset: int = 0
    ):
        from sqlalchemy import or_

        stmt = select(User)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(User.email.ilike(like), User.username.ilike(like), User.first_name.ilike(like)))
        if status:
            stmt = stmt.where(User.status == status)
        stmt = stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def count_users(self, *, query: str | None = None, status: str | None = None) -> int:
        from sqlalchemy import func, or_

        stmt = select(func.count()).select_from(User)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(User.email.ilike(like), User.username.ilike(like), User.first_name.ilike(like)))
        if status:
            stmt = stmt.where(User.status == status)
        result = await self.db.execute(stmt)
        return result.scalar_one()
