"""
AccountService — data export (Master Prompt §76) and secure account
deletion (§77).

Deletion is confirmed with an emailed OTP (works for password and Google
accounts alike), then, in ONE transaction:

  * every session is revoked;
  * trips the user owns are handed to a collaborator (or deleted if there
    are none); their memberships, comments, notes, votes elsewhere go;
  * private data is hard-deleted: memories, conversations + messages,
    attachments (metadata now, stored files right after commit),
    notifications, preferences, discovery searches, travel history,
    trials, overrides, OTPs, sessions, admin inbox;
  * the subscription is cancelled (billing rows kept for accounting);
  * emails recorded in email/login logs are redacted;
  * the `users` row is ANONYMIZED, not removed: identifiers/PII are
    replaced and the status becomes DELETED, so shared records and the
    legally required audit / security history keep valid references
    while no personal data remains.

Shared system data (destinations, places, RAG knowledge, provider
caches) is untouched — it does not belong to the user.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import OTPPurpose, SecurityEventSeverity, UploadUseCase, UserStatus
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.db.models.user import User
from app.modules.auth.otp_service import OTPService
from app.providers.email.interface import EmailProvider
from app.providers.storage.factory import get_storage_provider
from app.repositories.account_repository import AccountRepository
from app.repositories.admin_repository import AdminRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


def anonymized_identity(user_id: uuid.UUID) -> dict[str, Any]:
    """Placeholder values that keep every unique/non-null constraint
    satisfied without retaining anything about the person. Lengths fit the
    column sizes (username 20, phone 20). The email uses a subdomain of the
    IANA-reserved example.com: it is never deliverable, and unlike `.invalid`
    it still passes EmailStr validation, so admin user lists that serialize a
    deleted account do not fail."""
    tag = user_id.hex[:12]
    return {
        "email": f"deleted-{tag}@deleted.example.com",
        "username": f"deleted_{tag}",
        "first_name": "Deleted",
        "last_name": "User",
        "phone_number_e164": f"deleted:{tag[:10]}",
        "phone_country_code": "0",
        "phone_region_code": "ZZ",
    }


class AccountService:
    def __init__(self, db: AsyncSession, email_provider: Optional[EmailProvider] = None):
        self.db = db
        self.repo = AccountRepository(db)
        self.user_repo = UserRepository(db)
        self.admin_repo = AdminRepository(db)
        self.otp_service = OTPService(db, email_provider)

    # --- Export ---
    async def export_data(self, user: User) -> dict[str, Any]:
        return await self.repo.export_bundle(user)

    # --- Deletion ---
    async def request_deletion(self, user: User) -> None:
        await self._assert_deletable(user)
        await self.otp_service.issue_and_send(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            purpose=OTPPurpose.ACCOUNT_DELETION,
            enforce_cooldown=True,
        )
        await self.db.commit()

    async def confirm_deletion(self, user: User, *, otp_code: str, ip_address: Optional[str] = None) -> dict[str, int]:
        from app.modules.security.service import SecurityService

        await self._assert_deletable(user)
        await self.otp_service.verify(
            user_id=user.id, purpose=OTPPurpose.ACCOUNT_DELETION, submitted_code=otp_code
        )

        user_id = user.id
        original_email = user.email
        avatar_was_uploaded = bool(user.avatar_url and "cloudinary.com" in user.avatar_url)
        storage_keys = await self.repo.collect_attachment_storage_keys(user_id)

        summary, deleted_trip_ids = await self.repo.resolve_owned_trips(user_id)
        await self.repo.delete_personal_data(user_id, original_email)

        now = datetime.now(timezone.utc)
        for field, value in anonymized_identity(user_id).items():
            setattr(user, field, value)
        user.password_hash = None
        user.provider_subject_id = None
        user.avatar_url = None
        user.google_profile_picture_url = None
        user.status = UserStatus.DELETED
        user.is_active = False
        user.sessions_invalidated_at = now
        await self.user_repo.save(user)
        await self.db.commit()

        await SecurityService(self.db).record_event(
            user_id=user_id,
            event_type="account_deleted",
            severity=SecurityEventSeverity.INFO,
            ip_address=ip_address,
            metadata=summary,
        )
        await self._delete_stored_files(user_id, storage_keys, avatar_was_uploaded, deleted_trip_ids)
        return summary

    async def _assert_deletable(self, user: User) -> None:
        admin = await self.admin_repo.get_admin_by_user_id(user.id)
        if admin is not None and admin.is_active:
            raise ConflictError(
                "Admin accounts cannot be deleted directly. Ask a Super Admin to remove your admin role first."
            )

    @staticmethod
    async def _delete_stored_files(
        user_id: uuid.UUID,
        storage_keys: list[str],
        avatar_was_uploaded: bool,
        deleted_trip_ids: list[uuid.UUID],
    ) -> None:
        """Best effort AFTER the database commit: an orphaned object in
        storage is recoverable clean-up work, a half-deleted account is
        not. Failures are logged, never raised."""
        provider = get_storage_provider(UploadUseCase.ATTACHMENT)
        for key in storage_keys:
            try:
                await provider.delete(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("account_deletion_file_cleanup_failed", user_id=str(user_id), error=str(exc))
        # Files stored under the user's folder that have no database row
        # (direct /uploads/attachment and /uploads/travel-document uploads).
        for use_case in (UploadUseCase.ATTACHMENT, UploadUseCase.TRAVEL_DOCUMENT):
            try:
                await get_storage_provider(use_case).delete_prefix(f"users/{user_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("account_deletion_prefix_cleanup_failed", user_id=str(user_id), error=str(exc))
        # Stored PDFs of the trips that were deleted along with the account.
        for trip_id in deleted_trip_ids:
            try:
                await get_storage_provider(UploadUseCase.TRIP_PDF).delete_prefix(f"trips/{trip_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("account_deletion_trip_pdf_cleanup_failed", trip_id=str(trip_id), error=str(exc))
        if avatar_was_uploaded:
            try:
                await get_storage_provider(UploadUseCase.USER_AVATAR).delete(
                    f"{settings.CLOUDINARY_AVATAR_FOLDER}/{user_id}"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("account_deletion_avatar_cleanup_failed", user_id=str(user_id), error=str(exc))
