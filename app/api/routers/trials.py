"""Trial endpoints (Master Blueprint §49)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin_permission
from app.db.models.user import User
from app.db.session import get_db
from app.modules.trials.schemas import TrialConfigResponse, TrialConfigUpdateRequest, UserTrialResponse
from app.modules.trials.service import TrialService

router = APIRouter(prefix="/trials", tags=["Trials"])


@router.get("/config", response_model=TrialConfigResponse)
async def get_trial_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await TrialService(db).get_config()
    return TrialConfigResponse.model_validate(config, from_attributes=True)


@router.put(
    "/config", response_model=TrialConfigResponse,
    dependencies=[Depends(require_admin_permission("plans:manage"))],
)
async def update_trial_config(
    payload: TrialConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requires the 'plans:manage' admin permission — closed as of
    Phase 8; this was temporarily open to any authenticated user
    through Phases 6-7."""
    config = await TrialService(db).update_config(payload)
    return TrialConfigResponse.model_validate(config, from_attributes=True)


@router.get("/me", response_model=UserTrialResponse | None)
async def get_my_trial(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trial = await TrialService(db).get_my_trial(current_user.id)
    return UserTrialResponse.model_validate(trial) if trial else None


@router.post("/start", response_model=UserTrialResponse)
async def start_trial(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trial = await TrialService(db).start_trial(current_user.id)
    return UserTrialResponse.model_validate(trial)
