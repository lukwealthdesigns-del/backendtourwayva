"""
Auth endpoints — signup, OTP verification, login, token refresh/rotation,
logout, password reset/change, Google sign-in, username availability.
All OTPs are delivered via Brevo email; no verification/reset links are
ever sent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit
from app.core.config import settings
from app.core.rate_limit import enforce_rate_limit
from app.db.models.user import User
from app.db.session import get_db
from app.modules.auth.schemas import (
    AuthSuccessResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    GoogleAuthResponse,
    GoogleCompleteRequest,
    GooglePrefill,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UsernameAvailabilityResponse,
    UserPublic,
    VerifyOTPRequest,
)
from app.modules.auth.service import AuthService
from app.repositories.user_repository import UserRepository
from app.utils.username import InvalidUsernameError, validate_username

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _auth_success(user: User, access: str, refresh: str) -> AuthSuccessResponse:
    return AuthSuccessResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenResponse(access_token=access, refresh_token=refresh),
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit(bucket="auth:signup", max_requests=5, window_seconds=3600))])
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    accept_language: str | None = Header(default=None),
):
    """
    Create a new pending account (is_active=False) and email a 6-digit
    OTP for verification. The account cannot log in until verified.
    """
    service = AuthService(db)
    user = await service.signup(payload, client_ip=client_ip, accept_language=accept_language)
    return SignupResponse(
        message="Account created. Please check your email for a verification code.",
        email=user.email,
        wayva_id=user.wayva_id,
        otp_expiry_minutes=settings.OTP_EXPIRY_MINUTES,
    )


@router.post("/verify-email", response_model=AuthSuccessResponse,
             dependencies=[Depends(rate_limit(bucket="auth:verify-email", max_requests=10, window_seconds=300))])
async def verify_email(
    payload: VerifyOTPRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
):
    """Verify the signup OTP and activate the account. Issues tokens
    immediately on success so the user is logged in without a
    separate login step."""
    # Second, per-account limit: many IPs cannot pool their guesses.
    await enforce_rate_limit(
        key=f"auth:verify-email:acct:{payload.email.lower()}", max_requests=10, window_seconds=900
    )
    service = AuthService(db)
    user = await service.verify_email_otp(email=payload.email, otp_code=payload.otp_code)
    access, refresh = await service.start_session(user, ip_address=client_ip, user_agent=user_agent)
    return _auth_success(user, access, refresh)


@router.post("/resend-otp", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(bucket="auth:resend-otp", max_requests=3, window_seconds=300))])
async def resend_otp(payload: ResendOTPRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    await service.resend_email_verification_otp(email=payload.email)
    return {"message": "If an unverified account exists for this email, a new code has been sent."}


@router.post("/login", response_model=AuthSuccessResponse,
             dependencies=[Depends(rate_limit(bucket="auth:login", max_requests=10, window_seconds=300))])
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
):
    service = AuthService(db)
    user = await service.login(email=payload.login_id, password=payload.password, ip_address=client_ip, user_agent=user_agent)
    access, refresh = await service.start_session(user, ip_address=client_ip, user_agent=user_agent)
    return _auth_success(user, access, refresh)


@router.post("/refresh", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(bucket="auth:refresh", max_requests=30, window_seconds=300))])
async def refresh_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
):
    """Rotates the refresh token: the response carries a NEW refresh token
    and the one you sent stops working. Re-using an old one revokes the
    session."""
    service = AuthService(db)
    access, refresh = await service.refresh_session(
        payload.refresh_token, ip_address=client_ip, user_agent=user_agent
    )
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/logout", status_code=status.HTTP_200_OK,
             dependencies=[Depends(rate_limit(bucket="auth:logout", max_requests=30, window_seconds=300))])
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db)):
    """Revokes the session the given refresh token belongs to (this device).
    Idempotent. The short-lived access token simply expires."""
    await AuthService(db).logout(payload.refresh_token)
    return {"message": "Logged out."}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Signs out every device immediately (access AND refresh tokens)."""
    await AuthService(db).logout_all(current_user)
    return {"message": "Logged out of all devices."}


@router.post("/change-password", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(bucket="auth:change-password", max_requests=5, window_seconds=900, per="user"))])
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
):
    """Changes the password, signs out every other device, and returns
    fresh tokens for THIS device."""
    access, refresh = await AuthService(db).change_password(
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(bucket="auth:forgot-password", max_requests=3, window_seconds=900))])
async def forgot_password(payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Sends a 6-digit OTP (no link) if the account exists. Always
    returns the same generic response to avoid leaking account
    existence."""
    service = AuthService(db)
    await service.request_password_reset(email=payload.email)
    return {"message": "If an account exists for this email, a reset code has been sent."}


@router.post("/reset-password", status_code=status.HTTP_200_OK,
             dependencies=[Depends(rate_limit(bucket="auth:reset-password", max_requests=10, window_seconds=300))])
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit(
        key=f"auth:reset-password:acct:{payload.email.lower()}", max_requests=10, window_seconds=900
    )
    service = AuthService(db)
    await service.reset_password(
        email=payload.email, otp_code=payload.otp_code, new_password=payload.new_password
    )
    return {"message": "Password reset successfully. Please log in with your new password."}


# --- Google OAuth ---

@router.post("/google", response_model=GoogleAuthResponse,
             dependencies=[Depends(rate_limit(bucket="auth:google", max_requests=20, window_seconds=300))])
async def google_sign_in(
    payload: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
):
    """Sign in / sign up with a Google ID token (verified server-side).

    - existing or email-matched account  -> status "authenticated" + tokens
    - brand-new Google identity          -> status "profile_required" + a
      short-lived `signup_token`; collect a unique username and a phone
      number, then call POST /auth/google/complete.
    """
    service = AuthService(db)
    result = await service.google_authenticate(id_token=payload.id_token, ip_address=client_ip)
    if result.user is not None:
        access, refresh = await service.start_session(result.user, ip_address=client_ip, user_agent=user_agent)
        return GoogleAuthResponse(
            status="authenticated",
            user=UserPublic.model_validate(result.user),
            tokens=TokenResponse(access_token=access, refresh_token=refresh),
        )
    return GoogleAuthResponse(
        status="profile_required",
        signup_token=result.signup_token,
        signup_token_expires_in_seconds=settings.GOOGLE_SIGNUP_TOKEN_EXPIRE_MINUTES * 60,
        prefill=GooglePrefill(**(result.prefill or {})),
    )


@router.post("/google/complete", response_model=AuthSuccessResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit(bucket="auth:google-complete", max_requests=10, window_seconds=300))])
async def google_complete(
    payload: GoogleCompleteRequest,
    db: AsyncSession = Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    user_agent: str | None = Header(default=None),
    accept_language: str | None = Header(default=None),
):
    """Finishes a Google sign-up: username + phone number. The account is
    created active (Google already verified the email)."""
    service = AuthService(db)
    user = await service.google_complete(payload, client_ip=client_ip, accept_language=accept_language)
    access, refresh = await service.start_session(user, ip_address=client_ip, user_agent=user_agent)
    return _auth_success(user, access, refresh)


# --- Username availability (for live validation on signup forms) ---

@router.get("/username-available", response_model=UsernameAvailabilityResponse,
            dependencies=[Depends(rate_limit(bucket="auth:username-check", max_requests=30, window_seconds=60))])
async def username_available(
    username: str = Query(..., min_length=1, max_length=50),
    db: AsyncSession = Depends(get_db),
):
    try:
        normalized = validate_username(username)
    except InvalidUsernameError as exc:
        return UsernameAvailabilityResponse(username=username, available=False, reason=str(exc))
    taken = await UserRepository(db).username_exists(normalized)
    return UsernameAvailabilityResponse(
        username=normalized, available=not taken, reason="This username is already taken." if taken else None
    )
