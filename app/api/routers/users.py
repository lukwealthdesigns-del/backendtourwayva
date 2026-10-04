"""User profile, preferences, onboarding, sessions and account-lifecycle
endpoints. Every endpoint acts on the AUTHENTICATED user's own record —
the user id always comes from the verified token, never from the request."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_session_id, get_current_user, rate_limit
from app.db.models.user import User
from app.db.session import get_db
from app.modules.admin.messaging_service import AdminMessagingService
from app.modules.admin.schemas import AdminMessageResponse
from app.modules.users.account_service import AccountService
from app.modules.users.schemas import (
    DeleteAccountConfirmRequest,
    MePublic,
    OnboardingCompleteRequest,
    PreferencesResponse,
    PreferencesUpdateRequest,
    ProfileUpdateRequest,
    SessionResponse,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=MePublic)
async def get_me(current_user: User = Depends(get_current_user)):
    """Returns the authenticated user's own profile. Ownership is
    derived entirely from the verified JWT — never from a
    client-supplied user ID."""
    return MePublic.from_user(current_user)


@router.patch("/me", response_model=MePublic)
async def update_me(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update name, username, language, currency, timezone or country.
    Any other field is rejected. Email/phone changes require OTP
    re-verification and are not available here."""
    user = await UserService(db).update_profile(current_user, payload)
    return MePublic.from_user(user)


@router.delete("/me/avatar", response_model=MePublic)
async def delete_avatar(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Removes an uploaded avatar (falls back to the Google picture if the
    account has one)."""
    user = await UserService(db).remove_avatar(current_user)
    return MePublic.from_user(user)


# --- Preferences & onboarding (optional, skippable) ---

@router.get("/me/preferences", response_model=PreferencesResponse)
async def get_preferences(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    prefs = await UserService(db).get_preferences(current_user.id)
    return PreferencesResponse.model_validate(prefs) if prefs else PreferencesResponse()


@router.patch("/me/preferences", response_model=PreferencesResponse)
async def update_preferences(
    payload: PreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update — only the fields you send change. Everything here is
    voluntary; nothing is ever assumed about you."""
    prefs = await UserService(db).update_preferences(current_user.id, payload)
    return PreferencesResponse.model_validate(prefs)


@router.post("/me/onboarding/complete", response_model=MePublic)
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Call when the user finishes OR skips onboarding (`skipped: true`).
    Preferences can still be filled in later from the profile."""
    user = await UserService(db).complete_onboarding(current_user)
    return MePublic.from_user(user)


# --- Sessions / devices ---

@router.get("/me/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    current_session_id: Optional[str] = Depends(get_current_session_id),
    db: AsyncSession = Depends(get_db),
):
    sessions = await UserService(db).list_sessions(current_user.id)
    return [
        SessionResponse.model_validate(s).model_copy(update={"is_current": str(s.id) == current_session_id})
        for s in sessions
    ]


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Signs out one device. Its refresh token stops working immediately; an
    access token it already holds expires within minutes. Use
    POST /auth/logout-all for instant sign-out everywhere."""
    await UserService(db).revoke_session(user_id=current_user.id, session_id=session_id)
    return {"message": "Session revoked."}


# --- Account lifecycle: export and deletion ---

@router.get(
    "/me/export",
    dependencies=[Depends(rate_limit(bucket="users:export", max_requests=3, window_seconds=3600, per="user"))],
)
async def export_my_data(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Downloads everything Tour-Wayva holds about you as JSON: profile,
    preferences, memories, trips, conversations, history and more.
    Passwords and internal token data are never included."""
    bundle = await AccountService(db).export_data(current_user)
    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": f'attachment; filename="tour-wayva-export-{current_user.wayva_id}.json"'},
    )


@router.post(
    "/me/delete-account/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit(bucket="users:delete-request", max_requests=3, window_seconds=3600, per="user"))],
)
async def request_account_deletion(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Step 1: emails a 6-digit confirmation code (no link)."""
    await AccountService(db).request_deletion(current_user)
    return {"message": "A confirmation code has been sent to your email."}


@router.post(
    "/me/delete-account/confirm",
    dependencies=[Depends(rate_limit(bucket="users:delete-confirm", max_requests=5, window_seconds=900, per="user"))],
)
async def confirm_account_deletion(
    payload: DeleteAccountConfirmRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Step 2: with the code, permanently deletes/anonymizes the account.
    This cannot be undone."""
    summary = await AccountService(db).confirm_deletion(
        current_user, otp_code=payload.otp_code, ip_address=client_ip
    )
    return {"message": "Your account has been deleted.", **summary}


@router.get("/me/admin-messages", response_model=list[AdminMessageResponse])
async def get_my_admin_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Messages sent to you by Tour-Wayva staff/admins (Blueprint
    §52) — this reads YOUR OWN inbox only, no admin permission
    required."""
    messages = await AdminMessagingService(db).list_my_messages(current_user.id)
    return [AdminMessageResponse.model_validate(m) for m in messages]
