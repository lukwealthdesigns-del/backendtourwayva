"""Plan and subscription endpoints (Master Blueprint §48)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_admin_permission
from app.core.constants import AuditResult
from app.modules.admin.admin_service import AdminService
from app.db.models.user import User
from app.db.session import get_db
from app.modules.subscriptions.schemas import (
    PlanCreateRequest,
    PlanResponse,
    PlanUpdateRequest,
    SubscribeRequest,
    SubscriptionResponse,
)
from app.modules.subscriptions.service import SubscriptionService

router = APIRouter(tags=["Subscriptions"])


@router.post(
    "/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_permission("plans:manage"))],
)
async def create_plan(
    payload: PlanCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requires the 'plans:manage' admin permission — closed as of
    Phase 8; this was temporarily open to any authenticated user
    through Phases 6-7."""
    plan = await SubscriptionService(db).create_plan(payload)
    return PlanResponse.model_validate(plan)


@router.patch(
    "/plans/{plan_id}", response_model=PlanResponse,
    dependencies=[Depends(require_admin_permission("plans:manage"))],
)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdateRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Link the plan to its Paystack plan code or (de)activate it. Price,
    currency and interval cannot be changed (existing subscribers must never be
    silently re-priced). Audit-logged."""
    plan = await SubscriptionService(db).update_plan(plan_id, payload)
    await AdminService(db).log(
        admin_user_id=current_user.id,
        action="plan.update",
        target_type="plan",
        target_id=str(plan_id),
        result=AuditResult.SUCCESS,
        metadata=payload.model_dump(exclude_unset=True),
        ip_address=client_ip,
    )
    await db.commit()
    return PlanResponse.model_validate(plan)


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plans = await SubscriptionService(db).list_active_plans()
    return [PlanResponse.model_validate(p) for p in plans]


@router.get("/subscriptions/me", response_model=SubscriptionResponse | None)
async def get_my_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    subscription = await SubscriptionService(db).get_my_subscription(current_user.id)
    return SubscriptionResponse.model_validate(subscription) if subscription else None


@router.post("/subscriptions/subscribe", response_model=SubscriptionResponse)
async def subscribe(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service subscription to a FREE plan. Paid plans return 402
    Payment Required — they are activated only after a verified payment,
    never on request."""
    subscription = await SubscriptionService(db).subscribe(user_id=current_user.id, plan_id=payload.plan_id)
    return SubscriptionResponse.model_validate(subscription)


@router.post("/subscriptions/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Free plan: ends immediately. Paid plan: renewal is stopped (at Paystack
    too, for auto-renewing plans) and access continues until the period the
    user already paid for ends — `auto_renew` becomes false and `cancelled_at`
    is set."""
    subscription = await SubscriptionService(db).cancel_my_subscription(current_user.id)
    return SubscriptionResponse.model_validate(subscription)
