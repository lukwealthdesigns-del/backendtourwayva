"""
AuthService.

Signup / activation (updated requirement)
-----------------------------------------
  1. Signup collects: first_name, last_name, username, phone_number,
     email, password, confirm_password.
  2. Auto-derived: wayva_id, country, currency, language, timezone
     (via LocalizationService); the Google profile picture for Google
     sign-ups.
  3. An email/password user is created with is_active=False,
     status=PENDING.
  4. A 6-digit OTP is generated and emailed via Brevo — NOT a link.
  5. Only after OTP verification does is_active flip to True; the
     account then receives its launch-mode trial and tokens.

Google OAuth
------------
  POST /auth/google verifies the Google ID token SERVER-SIDE. Existing
  Google users are logged in; a Google identity whose email already has
  a Tour-Wayva account is linked to it; a brand-new Google identity gets
  a short-lived signed `signup_token` and must supply a unique username
  and a phone number (POST /auth/google/complete) — the same identity
  fields every other account has. Google has already verified the email,
  so no OTP is needed for Google accounts.

Sessions
--------
  Every login creates a UserSession. Refresh tokens are rotated on each
  use and re-use of a rotated token revokes the session (see
  app/db/models/session.py). `sessions_invalidated_at` on the user
  remains the "revoke everything" switch and is honoured by BOTH access
  and refresh tokens.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import AuthProvider, OTPPurpose, SecurityEventSeverity, UserStatus
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    RateLimitedError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.core.security import (
    TokenType,
    create_access_token,
    create_google_signup_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token_id,
    verify_password,
)
from app.db.models.session import UserSession
from app.db.models.user import User
from app.modules.auth.otp_service import OTPService
from app.modules.auth.schemas import GoogleCompleteRequest, SignupRequest
from app.providers.email.interface import EmailProvider
from app.providers.google.google_provider import GoogleJWKSVerifier
from app.providers.google.interface import GoogleIdentityVerifier
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.modules.security import suspicious_login
from app.utils.localization import resolve_localization
from app.utils.otp_generator import generate_wayva_id
from app.utils.phone import parse_and_validate_phone

logger = get_logger(__name__)


@dataclass
class GoogleAuthResult:
    """Outcome of POST /auth/google. Exactly one of the two shapes is set."""

    user: Optional[User] = None                    # authenticated
    signup_token: Optional[str] = None             # profile_required
    prefill: Optional[dict] = None


class AuthService:
    def __init__(
        self,
        db: AsyncSession,
        email_provider: Optional[EmailProvider] = None,
        google_verifier: Optional[GoogleIdentityVerifier] = None,
    ):
        self.db = db
        self.user_repo = UserRepository(db)
        self.session_repo = SessionRepository(db)
        self.otp_service = OTPService(db, email_provider)
        self.google_verifier = google_verifier or GoogleJWKSVerifier()

    # ------------------------------------------------------------------
    # Signup (email/password)
    # ------------------------------------------------------------------
    async def signup(
        self, payload: SignupRequest, client_ip: Optional[str] = None, accept_language: Optional[str] = None
    ) -> User:
        if await self.user_repo.email_exists(payload.email.lower()):
            raise ConflictError("An account with this email already exists.")

        if await self.user_repo.username_exists(payload.username):
            raise ConflictError("This username is already taken.")

        if await self.user_repo.phone_exists(payload.phone_number):
            raise ConflictError("An account with this phone number already exists.")

        parsed_phone = parse_and_validate_phone(payload.phone_number)
        localization = await resolve_localization(
            phone_region_code=parsed_phone.region_code, client_ip=client_ip, accept_language=accept_language
        )

        user = User(
            wayva_id=generate_wayva_id(),
            username=payload.username,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email.lower(),
            phone_number_e164=parsed_phone.e164,
            phone_country_code=parsed_phone.country_calling_code,
            phone_region_code=parsed_phone.region_code,
            password_hash=hash_password(payload.password),
            auth_provider=AuthProvider.EMAIL,
            status=UserStatus.PENDING,
            is_active=False,
            country=localization.country,
            currency=localization.currency,
            language=localization.language,
            timezone=localization.timezone,
        )
        await self._create_user(user)

        await self.otp_service.issue_and_send(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            purpose=OTPPurpose.EMAIL_VERIFICATION,
        )

        await self.db.commit()
        return user

    async def _create_user(self, user: User) -> None:
        """Insert with a race-safe uniqueness fallback: two concurrent
        signups can both pass the pre-checks; the database unique
        constraints then decide, and the loser gets a clean 409 instead
        of a 500."""
        try:
            await self.user_repo.create(user)
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError(
                "An account with this email, username or phone number already exists."
            ) from exc

    # ------------------------------------------------------------------
    # Email verification (activation)
    # ------------------------------------------------------------------
    async def verify_email_otp(self, *, email: str, otp_code: str) -> User:
        user = await self.user_repo.get_by_email(email)
        if user is None:
            raise UnauthorizedError("Invalid email or code.")

        if user.is_active:
            raise ConflictError("This account is already verified.")

        if user.status in (UserStatus.SUSPENDED, UserStatus.DELETED):
            raise ForbiddenError("This account is not available.")

        await self.otp_service.verify(
            user_id=user.id, purpose=OTPPurpose.EMAIL_VERIFICATION, submitted_code=otp_code
        )

        user.is_active = True
        user.status = UserStatus.ACTIVE
        user.email_verified_at = datetime.now(timezone.utc)
        user.last_login_at = user.email_verified_at
        await self.user_repo.save(user)
        await self.db.commit()

        await self._start_trial_best_effort(user)
        await self._welcome_best_effort(user)
        return user

    async def resend_email_verification_otp(self, *, email: str) -> None:
        user = await self.user_repo.get_by_email(email)
        if user is None:
            # Do not reveal account existence.
            return
        if user.is_active:
            raise ConflictError("This account is already verified.")

        try:
            await self.otp_service.issue_and_send(
                user_id=user.id, email=user.email, first_name=user.first_name,
                purpose=OTPPurpose.EMAIL_VERIFICATION, enforce_cooldown=True,
            )
        except RateLimitedError:
            return  # cooldown active: same generic response, no new email
        await self.db.commit()

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    async def login(self, *, email: str, password: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> User:
        from app.core.rate_limit import clear_failed_logins, is_login_locked_out, record_failed_login
        from app.modules.security.service import SecurityService

        security_service = SecurityService(self.db)

        if await security_service.is_ip_blocked(ip_address):
            raise ForbiddenError("Access from this network is currently blocked.")

        if await is_login_locked_out(email):
            raise RateLimitedError(
                "Too many failed login attempts. Please wait 15 minutes and try again, "
                "or reset your password."
            )

        user = await self.user_repo.get_by_email(email)
        if user is None or user.password_hash is None or not verify_password(password, user.password_hash):
            await record_failed_login(email)
            await security_service.record_login_attempt(
                email=email, ip_address=ip_address, success=False, user_agent=user_agent
            )
            raise UnauthorizedError("Invalid email or password.")

        if not user.is_active or user.status == UserStatus.PENDING:
            raise ForbiddenError(
                "Please verify your email before logging in.",
                details={"requires_verification": True, "email": user.email},
            )

        if user.status in (UserStatus.SUSPENDED, UserStatus.DELETED):
            raise ForbiddenError("This account is not available.")

        await clear_failed_logins(email)
        await security_service.record_login_attempt(
            email=email, ip_address=ip_address, success=True, user_agent=user_agent
        )

        if settings.SUSPICIOUS_LOGIN_DETECTION:
            try:
                await suspicious_login.check_and_record(
                    db=self.db, user=user, ip_address=ip_address, is_first_login=user.last_login_at is None
                )
            except Exception as exc:  # noqa: BLE001 - never blocks a login over analytics
                logger.warning("suspicious_login_check_failed", error=str(exc))

        user.last_login_at = datetime.now(timezone.utc)
        await self.user_repo.save(user)
        await self.db.commit()
        return user

    # ------------------------------------------------------------------
    # Password reset (OTP-based, no links) and change
    # ------------------------------------------------------------------
    async def request_password_reset(self, *, email: str) -> None:
        user = await self.user_repo.get_by_email(email)
        if user is None or user.status == UserStatus.DELETED:
            # Do not reveal account existence.
            return

        try:
            await self.otp_service.issue_and_send(
                user_id=user.id, email=user.email, first_name=user.first_name,
                purpose=OTPPurpose.PASSWORD_RESET, enforce_cooldown=True,
            )
        except RateLimitedError:
            return  # cooldown active: same generic response, no new email
        await self.db.commit()

    async def reset_password(self, *, email: str, otp_code: str, new_password: str) -> User:
        from app.core.rate_limit import clear_failed_logins

        user = await self.user_repo.get_by_email(email)
        if user is None or user.status == UserStatus.DELETED:
            raise UnauthorizedError("Invalid email or code.")

        await self.otp_service.verify(
            user_id=user.id, purpose=OTPPurpose.PASSWORD_RESET, submitted_code=otp_code
        )

        user.password_hash = hash_password(new_password)
        # Whoever held the old password (or a stolen session) must be
        # signed out everywhere once the password is reset.
        await self._revoke_all_sessions(user, reason="password_reset")
        await self.user_repo.save(user)
        await self.db.commit()
        await clear_failed_logins(email)
        return user

    async def change_password(
        self,
        *,
        user: User,
        current_password: str,
        new_password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[str, str]:
        """Authenticated password change. Signs out every OTHER device
        and returns fresh tokens for the current one."""
        from app.modules.security.service import SecurityService

        if user.password_hash is None:
            raise ValidationAppError(
                "This account signs in with Google and has no password yet. "
                "Use 'Forgot password' to set one."
            )
        if not verify_password(current_password, user.password_hash):
            raise ForbiddenError("Current password is incorrect.")
        if verify_password(new_password, user.password_hash):
            raise ValidationAppError("New password must be different from the current password.")

        user.password_hash = hash_password(new_password)
        await self._revoke_all_sessions(user, reason="password_changed")
        await self.user_repo.save(user)
        await self.db.commit()

        await SecurityService(self.db).record_event(
            user_id=user.id,
            event_type="password_changed",
            severity=SecurityEventSeverity.INFO,
            ip_address=ip_address,
        )
        return await self.start_session(user, ip_address=ip_address, user_agent=user_agent)

    # ------------------------------------------------------------------
    # Sessions and tokens
    # ------------------------------------------------------------------
    async def start_session(
        self, user: User, *, ip_address: Optional[str] = None, user_agent: Optional[str] = None
    ) -> tuple[str, str]:
        """Create a UserSession for this login and return
        (access_token, refresh_token). The refresh token's id (hashed) is
        the session's rotation anchor."""
        now = datetime.now(timezone.utc)
        session_id = uuid.uuid4()
        jti = str(uuid.uuid4())

        session = UserSession(
            id=session_id,
            user_id=user.id,
            refresh_jti_hash=hash_token_id(jti),
            user_agent=(user_agent or "")[:500] or None,
            ip_address=ip_address,
            last_used_at=now,
            expires_at=now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        )
        await self.session_repo.create(session)
        await self.session_repo.enforce_session_cap(user.id, settings.MAX_ACTIVE_SESSIONS_PER_USER)
        await self.db.commit()

        access = create_access_token(
            str(user.id), extra_claims={"role": user.role.value, "sid": str(session_id)}
        )
        refresh = create_refresh_token(str(user.id), jti=jti)
        return access, refresh

    async def refresh_session(
        self, refresh_token: str, *, ip_address: Optional[str] = None, user_agent: Optional[str] = None
    ) -> tuple[str, str]:
        """Rotate the refresh token: validate it against its session, issue
        a NEW refresh token (the old one stops working) and a new access
        token."""
        from app.modules.security.service import SecurityService

        payload = decode_token(refresh_token, TokenType.REFRESH)
        jti = payload.get("jti")
        try:
            user_id = uuid.UUID(payload["sub"])
        except (ValueError, KeyError) as exc:
            raise UnauthorizedError("Invalid refresh token.") from exc
        if not jti:
            raise UnauthorizedError("Invalid refresh token.")

        presented_hash = hash_token_id(jti)
        session = await self.session_repo.get_by_any_hash(presented_hash)
        if session is None or session.user_id != user_id:
            raise UnauthorizedError("Invalid refresh token.")

        now = datetime.now(timezone.utc)
        if session.revoked_at is not None or session.expires_at <= now:
            raise UnauthorizedError("Session has expired. Please log in again.")

        if session.refresh_jti_hash != presented_hash:
            # A token that was ALREADY rotated is being presented again.
            within_grace = (
                session.rotated_at is not None
                and (now - session.rotated_at).total_seconds() <= settings.REFRESH_REUSE_GRACE_SECONDS
            )
            if not within_grace:
                # Genuine re-use => the token was copied. Kill this session.
                session.revoked_at = now
                session.revoked_reason = "refresh_token_reuse"
                await self.session_repo.save(session)
                await self.db.commit()
                await SecurityService(self.db).record_event(
                    user_id=user_id,
                    event_type="refresh_token_reuse_detected",
                    severity=SecurityEventSeverity.WARNING,
                    ip_address=ip_address,
                    metadata={"session_id": str(session.id)},
                )
            raise UnauthorizedError("Invalid refresh token.")

        user = await self.user_repo.get_by_id(user_id)
        if user is None or not user.is_active or user.status in (UserStatus.SUSPENDED, UserStatus.DELETED):
            raise UnauthorizedError("Invalid refresh token.")

        # Honour "revoke all sessions" for refresh tokens too — otherwise a
        # revoked user could keep minting fresh access tokens for 30 days.
        issued_at = payload.get("iat")
        if user.sessions_invalidated_at is not None and (
            issued_at is None or int(issued_at) < int(user.sessions_invalidated_at.timestamp())
        ):
            raise UnauthorizedError("Session has been revoked. Please log in again.")

        new_jti = str(uuid.uuid4())
        session.previous_jti_hash = session.refresh_jti_hash
        session.refresh_jti_hash = hash_token_id(new_jti)
        session.rotated_at = now
        session.last_used_at = now
        session.expires_at = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        if ip_address:
            session.ip_address = ip_address
        if user_agent:
            session.user_agent = user_agent[:500]
        await self.session_repo.save(session)
        await self.db.commit()

        access = create_access_token(
            str(user.id), extra_claims={"role": user.role.value, "sid": str(session.id)}
        )
        return access, create_refresh_token(str(user.id), jti=new_jti)

    async def logout(self, refresh_token: str) -> None:
        """Revoke the session a refresh token belongs to. Idempotent: an
        invalid/expired/unknown token means there is nothing to revoke."""
        try:
            payload = decode_token(refresh_token, TokenType.REFRESH)
        except UnauthorizedError:
            return
        jti = payload.get("jti")
        if not jti:
            return
        session = await self.session_repo.get_by_any_hash(hash_token_id(jti))
        if session is None or str(session.user_id) != payload.get("sub") or session.revoked_at is not None:
            return
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = "logout"
        await self.session_repo.save(session)
        await self.db.commit()
        # Same immediate-invalidation reasoning as UserService.revoke_session:
        # the row above already stops the refresh token; this stops an
        # already-issued access token for THIS session too, instead of
        # leaving it valid until its natural ≤30-minute expiry.
        from app.core.session_blocklist import blocklist_session

        await blocklist_session(str(session.id))

    async def logout_all(self, user: User) -> None:
        await self._revoke_all_sessions(user, reason="logout_all")
        await self.user_repo.save(user)
        await self.db.commit()

    async def _revoke_all_sessions(self, user: User, *, reason: str) -> None:
        """Revoke every session row and move the user's revocation cutoff
        forward so already-issued ACCESS tokens die immediately too.
        Caller commits."""
        await self.session_repo.revoke_all_for_user(user.id, reason)
        user.sessions_invalidated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Google OAuth
    # ------------------------------------------------------------------
    async def google_authenticate(
        self, *, id_token: str, ip_address: Optional[str] = None
    ) -> GoogleAuthResult:
        from app.modules.security.service import SecurityService

        identity = await self.google_verifier.verify(id_token)
        now = datetime.now(timezone.utc)

        # 1) Returning Google user.
        user = await self.user_repo.get_by_provider_subject(AuthProvider.GOOGLE, identity.subject)
        if user is not None:
            self._assert_account_usable(user)
            user.last_login_at = now
            user.google_profile_picture_url = identity.picture or user.google_profile_picture_url
            if not user.avatar_url and identity.picture:
                user.avatar_url = identity.picture
            await self.user_repo.save(user)
            await self.db.commit()
            return GoogleAuthResult(user=user)

        # 2) Google identity whose (Google-verified) email already has an account: link it.
        existing = await self.user_repo.get_by_email(identity.email)
        if existing is not None:
            if existing.status in (UserStatus.SUSPENDED, UserStatus.DELETED):
                raise ForbiddenError("This account is not available.")
            if existing.provider_subject_id and existing.provider_subject_id != identity.subject:
                raise ConflictError("This account is already linked to a different Google account.")

            was_unverified = not existing.is_active
            existing.provider_subject_id = identity.subject
            existing.auth_provider = AuthProvider.GOOGLE
            if was_unverified:
                # Someone registered this email with a password but never
                # proved they own it. Google just proved the real owner does,
                # so drop that unverified password — otherwise the squatter
                # could keep logging in to the real owner's account.
                existing.password_hash = None
                existing.is_active = True
                existing.status = UserStatus.ACTIVE
                existing.email_verified_at = now
            existing.google_profile_picture_url = identity.picture
            if not existing.avatar_url and identity.picture:
                existing.avatar_url = identity.picture
            existing.last_login_at = now
            await self.user_repo.save(existing)
            await self.db.commit()

            await SecurityService(self.db).record_event(
                user_id=existing.id,
                event_type="google_account_linked",
                severity=SecurityEventSeverity.INFO,
                ip_address=ip_address,
                metadata={"activated_unverified_account": was_unverified},
            )
            if was_unverified:
                await self._start_trial_best_effort(existing)
            return GoogleAuthResult(user=existing)

        # 3) Brand-new Google identity: needs a username + phone first.
        prefill = {
            "first_name": identity.given_name,
            "last_name": identity.family_name,
            "email": identity.email,
            "picture": identity.picture,
        }
        signup_token = create_google_signup_token(
            {
                "sub": identity.subject,
                "email": identity.email,
                "given_name": identity.given_name,
                "family_name": identity.family_name,
                "picture": identity.picture,
            }
        )
        return GoogleAuthResult(signup_token=signup_token, prefill=prefill)

    async def google_complete(
        self, payload: GoogleCompleteRequest, *, client_ip: Optional[str] = None, accept_language: Optional[str] = None
    ) -> User:
        claims = decode_token(payload.signup_token, TokenType.GOOGLE_SIGNUP)
        subject = claims.get("sub")
        email = (claims.get("email") or "").lower()
        if not subject or not email:
            raise UnauthorizedError("Invalid signup token.")

        if await self.user_repo.get_by_provider_subject(AuthProvider.GOOGLE, subject) is not None:
            raise ConflictError("This Google account is already registered. Please sign in.")
        if await self.user_repo.email_exists(email):
            raise ConflictError("An account with this email already exists. Please sign in with Google again.")
        if await self.user_repo.username_exists(payload.username):
            raise ConflictError("This username is already taken.")
        if await self.user_repo.phone_exists(payload.phone_number):
            raise ConflictError("An account with this phone number already exists.")

        first_name = payload.first_name or claims.get("given_name")
        last_name = payload.last_name or claims.get("family_name")
        if not first_name or not last_name:
            raise ValidationAppError("Please provide your first and last name.")

        parsed_phone = parse_and_validate_phone(payload.phone_number)
        localization = await resolve_localization(
            phone_region_code=parsed_phone.region_code, client_ip=client_ip, accept_language=accept_language
        )
        now = datetime.now(timezone.utc)
        picture = claims.get("picture")

        user = User(
            wayva_id=generate_wayva_id(),
            username=payload.username,
            first_name=first_name[:100],
            last_name=last_name[:100],
            email=email,
            phone_number_e164=parsed_phone.e164,
            phone_country_code=parsed_phone.country_calling_code,
            phone_region_code=parsed_phone.region_code,
            password_hash=None,
            auth_provider=AuthProvider.GOOGLE,
            provider_subject_id=subject,
            status=UserStatus.ACTIVE,
            is_active=True,
            email_verified_at=now,
            last_login_at=now,
            country=localization.country,
            currency=localization.currency,
            language=localization.language,
            timezone=localization.timezone,
            google_profile_picture_url=picture,
            avatar_url=picture,
        )
        await self._create_user(user)
        await self.db.commit()

        await self._start_trial_best_effort(user)
        await self._welcome_best_effort(user)
        return user

    @staticmethod
    def _assert_account_usable(user: User) -> None:
        if not user.is_active or user.status in (UserStatus.SUSPENDED, UserStatus.DELETED, UserStatus.PENDING):
            raise ForbiddenError("This account is not available.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _welcome_best_effort(self, user: User) -> None:
        """Welcome notification + email, once, when an account first becomes usable. Never blocks or fails sign-in."""
        from app.modules.notifications.welcome import send_welcome

        await send_welcome(self.db, user)
        try:
            await self.db.refresh(user)  # a rollback inside send_welcome expires `user`; keep it usable for token issuing
        except Exception:  # noqa: BLE001
            pass

    async def _start_trial_best_effort(self, user: User) -> None:
        """Launch-mode trial (Blueprint §49). Must never block sign-in, so
        any failure is logged and swallowed — after rolling the session
        back (and re-loading `user`, which a rollback expires) so both stay
        usable for the token-issuing step that follows."""
        user_id = user.id
        try:
            from app.modules.trials.service import TrialService

            await TrialService(self.db).start_trial_if_eligible(user_id)
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            await self.db.refresh(user)
            logger.warning("trial_auto_start_failed", user_id=str(user_id), error=str(exc))
