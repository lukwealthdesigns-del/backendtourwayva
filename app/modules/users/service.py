"""UserService — profile, preferences, onboarding and session management
for the authenticated user (always operating on the caller's OWN record;
the user id is never taken from the request)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import UploadUseCase
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models.preferences import UserPreferences
from app.db.models.session import UserSession
from app.db.models.user import User
from app.modules.users.schemas import PreferencesUpdateRequest, ProfileUpdateRequest
from app.providers.storage.factory import get_storage_provider
from app.repositories.preferences_repository import PreferencesRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.prefs_repo = PreferencesRepository(db)
        self.session_repo = SessionRepository(db)

    # --- Profile ---
    async def update_profile(self, user: User, payload: ProfileUpdateRequest) -> User:
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)

        new_username = changes.get("username")
        if new_username and new_username != user.username:
            if await self.user_repo.username_taken_by_other(new_username, user.id):
                raise ConflictError("This username is already taken.")

        for field, value in changes.items():
            setattr(user, field, value)

        try:
            await self.user_repo.save(user)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError("This username is already taken.") from exc
        return user

    async def remove_avatar(self, user: User) -> User:
        """Deletes an uploaded (Cloudinary) avatar and falls back to the
        Google profile picture, if the account has one."""
        current = user.avatar_url
        if current and "cloudinary.com" in current:
            key = f"{settings.CLOUDINARY_AVATAR_FOLDER}/{user.id}"
            try:
                await get_storage_provider(UploadUseCase.USER_AVATAR).delete(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("avatar_delete_failed", user_id=str(user.id), error=str(exc))
        user.avatar_url = user.google_profile_picture_url
        await self.user_repo.save(user)
        await self.db.commit()
        return user

    # --- Preferences ---
    async def get_preferences(self, user_id: uuid.UUID) -> Optional[UserPreferences]:
        return await self.prefs_repo.get(user_id)

    async def update_preferences(self, user_id: uuid.UUID, payload: PreferencesUpdateRequest) -> UserPreferences:
        changes = payload.model_dump(exclude_unset=True)
        prefs = await self.prefs_repo.get(user_id)
        if prefs is None:
            prefs = await self.prefs_repo.create(
                UserPreferences(
                    user_id=user_id,
                    travel_styles=[],
                    interests=[],
                    dietary_preferences=[],
                    accessibility_preferences=[],
                )
            )
        list_fields = {"travel_styles", "interests", "dietary_preferences", "accessibility_preferences"}
        for field, value in changes.items():
            if field in list_fields and value is None:
                value = []
            setattr(prefs, field, value)
        await self.prefs_repo.save(prefs)
        await self.db.commit()
        return prefs

    async def complete_onboarding(self, user: User) -> User:
        """Marks onboarding done — used both when the user finishes it and
        when they skip it (it is optional; they can fill in preferences
        later from their profile)."""
        if not user.onboarding_completed:
            user.onboarding_completed = True
            await self.user_repo.save(user)
            await self.db.commit()
        return user

    # --- Sessions ---
    async def list_sessions(self, user_id: uuid.UUID) -> list[UserSession]:
        return await self.session_repo.list_active_for_user(user_id)

    async def revoke_session(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        from datetime import datetime, timezone

        from app.core.session_blocklist import blocklist_session

        session = await self.session_repo.get_for_user(session_id, user_id)
        if session is None or session.revoked_at is not None:
            raise NotFoundError("Session not found.")
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = "revoked_by_user"
        await self.session_repo.save(session)
        await self.db.commit()
        # Best-effort: the row above is the source of truth (a future
        # refresh-token use already fails against it); this just makes an
        # ALREADY-ISSUED access token for this one device stop working
        # immediately instead of at its natural ≤30-minute expiry.
        await blocklist_session(str(session_id))
