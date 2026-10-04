"""Entitlement endpoints (Master Blueprint §48, §50).

Read-only for users. Feature overrides can only be granted or revoked by an
admin holding `plans:manage` — see PUT /admin/users/{user_id}/feature-overrides.
There is deliberately NO endpoint that lets a user change their own
entitlements."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.modules.entitlements.schemas import EntitlementsResponse
from app.modules.entitlements.service import EntitlementService

router = APIRouter(prefix="/entitlements", tags=["Entitlements"])


@router.get("/me", response_model=EntitlementsResponse)
async def get_my_entitlements(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolved feature-flag access for the current user. Precedence: global kill
    switch > per-user override > open-to-all > trial > subscription > free tier
    (see app/modules/entitlements/rules.py). `disabled_globally` lists features an
    admin has switched off for everyone."""
    service = EntitlementService(db)
    flags, source = await service.resolve_all(current_user.id)
    return EntitlementsResponse(flags=flags, source=source, disabled_globally=await service.globally_disabled())
