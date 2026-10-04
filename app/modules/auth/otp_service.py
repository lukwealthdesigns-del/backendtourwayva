"""
OTPService — shared logic for issuing and verifying OTP codes for
email verification, password reset and account deletion (Brevo-
delivered, no links, per the requested adjustment).

Security notes
--------------
* Failed-attempt counters MUST be committed BEFORE the error is raised.
  The request-scoped session (`get_db`) rolls back on any exception, so
  an increment that is only flushed is silently discarded and the
  attempt limit never triggers. `verify` therefore commits explicitly on
  the failure path.
* Once the attempt limit is reached the code is burned (consumed) so the
  user must request a new one — guessing cannot continue against it.
* The plaintext code is never stored, only its bcrypt hash.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import OTPPurpose
from app.core.exceptions import (
    OTPExpiredError,
    OTPInvalidError,
    OTPMaxAttemptsError,
    RateLimitedError,
)
from app.core.security import hash_password, verify_password
from app.db.models.otp import OTPCode
from app.providers.email.factory import get_email_provider
from app.providers.email.interface import EmailProvider
from app.repositories.otp_repository import OTPRepository
from app.utils.otp_generator import generate_numeric_otp

_PURPOSE_LABELS = {
    OTPPurpose.EMAIL_VERIFICATION: "email verification",
    OTPPurpose.PASSWORD_RESET: "password reset",
    OTPPurpose.PHONE_VERIFICATION: "phone verification",
    OTPPurpose.LOGIN_2FA: "login",
    OTPPurpose.ACCOUNT_DELETION: "account deletion",
}


class OTPService:
    def __init__(self, db: AsyncSession, email_provider: Optional[EmailProvider] = None):
        self.db = db
        self.otp_repo = OTPRepository(db)
        self.email_provider = email_provider or get_email_provider(db)

    async def issue_and_send(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        first_name: str,
        purpose: OTPPurpose,
        enforce_cooldown: bool = False,
    ) -> None:
        """Invalidate any existing active OTP for this purpose, generate a
        fresh one, store its hash, and email it via Brevo.

        With `enforce_cooldown=True`, raises RateLimitedError if a code
        for this purpose was created less than OTP_RESEND_COOLDOWN_SECONDS
        ago (used by the resend / forgot-password / delete-request paths)."""
        if enforce_cooldown:
            latest = await self.otp_repo.get_latest_any(user_id, purpose)
            if latest is not None:
                elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
                if elapsed < settings.OTP_RESEND_COOLDOWN_SECONDS:
                    raise RateLimitedError(
                        "Please wait a moment before requesting another code.",
                        details={"retry_after_seconds": int(settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed)},
                    )

        await self.otp_repo.invalidate_active(user_id, purpose)

        code = generate_numeric_otp()
        otp = OTPCode(
            user_id=user_id,
            purpose=purpose,
            code_hash=hash_password(code),
            destination=email,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
            attempts=0,
        )
        await self.otp_repo.create(otp)

        await self.email_provider.send_otp_email(
            to_email=email,
            first_name=first_name,
            otp_code=code,
            purpose_label=_PURPOSE_LABELS.get(purpose, "verification"),
        )

    async def verify(self, *, user_id: uuid.UUID, purpose: OTPPurpose, submitted_code: str) -> None:
        """Verify a submitted OTP code. Raises a controlled AppError
        subclass on any failure; returns None (silently) on success —
        caller proceeds to activate the account / allow the reset."""
        otp = await self.otp_repo.get_latest_active(user_id, purpose)
        if otp is None:
            raise OTPInvalidError("No active verification code found. Please request a new one.")

        if otp.expires_at < datetime.now(timezone.utc):
            raise OTPExpiredError("This code has expired. Please request a new one.")

        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise OTPMaxAttemptsError("Too many incorrect attempts. Please request a new code.")

        if not verify_password(submitted_code, otp.code_hash):
            otp.attempts += 1
            limit_reached = otp.attempts >= settings.OTP_MAX_ATTEMPTS
            if limit_reached:
                otp.consumed_at = datetime.now(timezone.utc)  # burn the code
            await self.otp_repo.save(otp)
            # Persist BEFORE raising: get_db rolls back on exceptions.
            await self.db.commit()
            if limit_reached:
                raise OTPMaxAttemptsError("Too many incorrect attempts. Please request a new code.")
            raise OTPInvalidError("Incorrect code. Please try again.")

        otp.consumed_at = datetime.now(timezone.utc)
        await self.otp_repo.save(otp)
