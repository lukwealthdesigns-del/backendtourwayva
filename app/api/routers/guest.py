"""Guest mode endpoints: start a session, read its allowances, and claim its work into a real account."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit
from app.db.models.user import User
from app.db.session import get_db
from app.modules.guest.rules import GUEST_SESSION_HEADER
from app.modules.guest.schemas import ClaimResponse, GuestSessionCreate, GuestSessionResponse
from app.modules.guest.service import GuestService

router = APIRouter(prefix="/guest", tags=["Guest"])


@router.post(
    "/session", response_model=GuestSessionResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(bucket="guest:create", max_requests=10, window_seconds=3600, per="ip"))],
)
async def create_guest_session(
    payload: GuestSessionCreate, client_ip: Optional[str] = Depends(get_client_ip), db: AsyncSession = Depends(get_db),
):
    """Start a guest session. The response carries the token ONCE: keep it and send it as `X-Guest-Session` on later
    requests. It identifies the guest only; it grants nothing an account would."""
    return await GuestService(db).create_session(client_ip=client_ip, language=payload.language, currency=payload.currency, country=payload.country)


@router.get("/session", response_model=GuestSessionResponse)
async def guest_session_status(request: Request, db: AsyncSession = Depends(get_db)):
    """The guest's expiry, allowances and usage (read from the server: the app never decides them)."""
    service = GuestService(db)
    session, _user = await service.authenticate(request.headers.get(GUEST_SESSION_HEADER))
    return await service.status(session)


@router.post(
    "/claim", response_model=ClaimResponse,
    dependencies=[Depends(rate_limit(bucket="guest:claim", max_requests=20, window_seconds=300, per="user"))],
)
async def claim_guest_session(
    guest_token: Optional[str] = Header(default=None, alias=GUEST_SESSION_HEADER),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Move the guest's trips, Companion conversations and Discover searches into the signed-in account. Requires
    sign-in, is all-or-nothing, and is safe to repeat: a second call returns the same result without duplicating anything."""
    return await GuestService(db).claim(token=guest_token, user=current_user)
