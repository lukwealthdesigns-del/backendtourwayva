"""Admin endpoints (Master Blueprint §51-56, §89 partial)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit, require_admin_permission
from app.core.admin_permissions import ALL_PERMISSION_CODES
from app.core.constants import FREE_TIER_DEFAULT_FLAGS, AuditResult, FeatureFlag, PaymentStatus, SubscriptionStatus, UserStatus
from app.core.exceptions import NotFoundError, ValidationAppError
from app.db.models.monetization import Subscription, UserTrial
from app.db.models.user import User
from app.db.session import get_db
from app.modules.admin.admin_service import AdminService
from app.modules.admin.broadcast_service import BroadcastService
from app.modules.admin.messaging_service import AdminMessagingService
from app.modules.admin.schemas import (
    AdminMessageResponse,
    AdminUserResponse,
    AICostByTripResponse,
    AICostByUserResponse,
    AuditLogResponse,
    BlockUserRequest,
    BroadcastResponse,
    CacheStatsResponse,
    CreateAdminRequest,
    CreateBroadcastRequest,
    DailyMetricsResponse,
    DashboardStatsResponse,
    FeatureFlagStateResponse,
    FeatureFlagUpdateRequest,
    PermissionResponse,
    RevenueDashboardResponse,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
    SendMessageRequest,
    SetAdminRolesRequest,
    UserDetailResponse,
    UserListResponse,
    UserSummaryResponse,
)
from app.modules.admin.user_management_service import UserManagementService
from app.modules.entitlements.schemas import EntitlementsResponse, OverrideRequest
from app.modules.entitlements.service import EntitlementService
from app.modules.lifecycle.metrics import MetricsService
from app.modules.planning.policy import PlanningPolicyService
from app.modules.planning.policy_schemas import PlanningPolicyConfigResponse, PlanningPolicyUpdate
from app.modules.payments.schemas import (
    AdminPaymentListResponse,
    AdminPaymentResponse,
    RefundRequest,
    RefundResponse,
)
from app.modules.payments.service import PaymentService
from app.repositories.monetization_repository import MonetizationRepository
from app.repositories.user_repository import UserRepository

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(rate_limit(bucket="admin", max_requests=60, window_seconds=60, per="user"))],
)


# --- Admin roster & RBAC (Super Admin only for mutations) ---

def _admin_response(summary) -> AdminUserResponse:
    return AdminUserResponse(
        id=summary.admin.id, user_id=summary.admin.user_id, is_active=summary.admin.is_active,
        created_at=summary.admin.created_at, roles=list(summary.access.role_names),
        is_super=summary.access.is_super, permissions=sorted(summary.access.effective_permissions()),
    )


def _role_response(summary) -> RoleResponse:
    return RoleResponse(
        id=summary.role.id, name=summary.role.name, description=summary.role.description,
        is_system=summary.role.is_system, is_super=summary.role.is_super,
        permissions=sorted(ALL_PERMISSION_CODES if summary.role.is_super else summary.permissions & ALL_PERMISSION_CODES),
        admin_count=summary.admin_count,
    )


@router.get("/me", response_model=AdminUserResponse)
async def admin_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The caller's admin identity and effective permissions (for building an admin UI)."""
    return _admin_response(await AdminService(db).my_access(current_user.id))


@router.post("/admins", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_admin(
    payload: CreateAdminRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. Grants admin access to an existing, active user with one or more roles."""
    summary = await AdminService(db).create_admin(
        actor_id=current_user.id, target_user_id=payload.user_id, role_names=payload.roles, ip_address=client_ip
    )
    return _admin_response(summary)


@router.get("/admins", response_model=list[AdminUserResponse])
async def list_admins(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [_admin_response(a) for a in await AdminService(db).list_admins(current_user.id)]


@router.put("/admins/{user_id}/roles", response_model=AdminUserResponse)
async def set_admin_roles(
    user_id: uuid.UUID,
    payload: SetAdminRolesRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. REPLACES the admin's roles (at least one). The last active
    Super Admin cannot be demoted. Takes effect on the admin's very next request."""
    summary = await AdminService(db).set_admin_roles(
        actor_id=current_user.id, target_user_id=user_id, role_names=payload.roles, ip_address=client_ip
    )
    return _admin_response(summary)


@router.delete("/admins/{user_id}", response_model=AdminUserResponse)
async def disable_admin(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. Disables (does not delete) an admin's access. You cannot disable
    yourself, and the last active Super Admin cannot be disabled."""
    service = AdminService(db)
    await service.disable_admin(actor_id=current_user.id, target_user_id=user_id, ip_address=client_ip)
    return _admin_response(next(a for a in await service.list_admins(current_user.id) if a.admin.user_id == user_id))


@router.get("/permissions", response_model=list[PermissionResponse])
async def list_permissions(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Every permission the platform checks — roles are composed from these."""
    return [PermissionResponse.model_validate(p) for p in await AdminService(db).list_permissions(current_user.id)]


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [_role_response(r) for r in await AdminService(db).list_roles(current_user.id)]


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreateRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. Creates a custom role from catalog permissions."""
    summary = await AdminService(db).create_role(
        actor_id=current_user.id, name=payload.name, description=payload.description,
        permissions=payload.permissions, ip_address=client_ip,
    )
    return _role_response(summary)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. Changes a role's description and/or REPLACES its permissions;
    every admin holding the role is affected immediately. The super role cannot be edited."""
    summary = await AdminService(db).update_role(
        actor_id=current_user.id, role_id=role_id, description=payload.description,
        permissions=payload.permissions, ip_address=client_ip,
    )
    return _role_response(summary)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Super Admin only. System roles and roles still held by an admin cannot be deleted."""
    await AdminService(db).delete_role(actor_id=current_user.id, role_id=role_id, ip_address=client_ip)


# --- User management ---

@router.get("/users", response_model=UserListResponse)
async def search_users(
    query: Optional[str] = Query(default=None, min_length=1, max_length=255),
    status_filter: Optional[UserStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    users, total = await UserManagementService(db).search_users(
        actor_id=current_user.id,
        query=query,
        status=status_filter.value if status_filter else None,
        limit=limit,
        offset=offset,
    )
    results = [UserSummaryResponse.model_validate(u) for u in users]
    return UserListResponse(results=results, count=len(results), total=total)


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await UserManagementService(db).get_user_detail(actor_id=current_user.id, target_user_id=user_id)
    return UserDetailResponse(
        **UserSummaryResponse.model_validate(detail["user"]).model_dump(),
        country=detail["user"].country,
        currency=detail["user"].currency,
        last_login_at=detail["user"].last_login_at,
        has_active_subscription=detail["has_active_subscription"],
        has_active_trial=detail["has_active_trial"],
    )


@router.post("/users/{user_id}/block", response_model=UserSummaryResponse)
async def block_user(
    user_id: uuid.UUID,
    payload: BlockUserRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    user = await UserManagementService(db).block_user(
        actor_id=current_user.id, target_user_id=user_id, reason=payload.reason, ip_address=client_ip
    )
    return UserSummaryResponse.model_validate(user)


@router.post("/users/{user_id}/unblock", response_model=UserSummaryResponse)
async def unblock_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    user = await UserManagementService(db).unblock_user(
        actor_id=current_user.id, target_user_id=user_id, ip_address=client_ip
    )
    return UserSummaryResponse.model_validate(user)


@router.delete("/users/{user_id}", response_model=UserSummaryResponse)
async def delete_user(
    user_id: uuid.UUID,
    payload: BlockUserRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete — see UserManagementService.delete_user docstring."""
    user = await UserManagementService(db).delete_user(
        actor_id=current_user.id, target_user_id=user_id, reason=payload.reason, ip_address=client_ip
    )
    return UserSummaryResponse.model_validate(user)


@router.post("/users/{user_id}/revoke-sessions", response_model=UserSummaryResponse)
async def revoke_sessions(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    user = await UserManagementService(db).revoke_sessions(
        actor_id=current_user.id, target_user_id=user_id, ip_address=client_ip
    )
    return UserSummaryResponse.model_validate(user)


# --- Entitlement overrides (admin only — users can never grant themselves features) ---

@router.put("/users/{user_id}/feature-overrides", response_model=EntitlementsResponse)
async def set_user_feature_override(
    user_id: uuid.UUID,
    payload: OverrideRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Grant or revoke ONE feature flag for a specific user. Requires the
    `plans:manage` admin permission; every change is audit-logged."""
    admin_service = AdminService(db)
    await admin_service.require_permission(user_id=current_user.id, permission="plans:manage")

    if await UserRepository(db).get_by_id(user_id) is None:
        raise NotFoundError("User not found.")
    try:
        flag = FeatureFlag(payload.flag)
    except ValueError as exc:
        raise ValidationAppError(f"Unknown feature flag '{payload.flag}'.") from exc

    entitlement_service = EntitlementService(db)
    await entitlement_service.set_override(user_id=user_id, flag=flag, is_enabled=payload.is_enabled)
    await admin_service.log(
        admin_user_id=current_user.id,
        action="feature_override.set",
        target_type="user",
        target_id=str(user_id),
        result=AuditResult.SUCCESS,
        metadata={"flag": flag.value, "is_enabled": payload.is_enabled},
        ip_address=client_ip,
    )
    await db.commit()

    flags, source = await entitlement_service.resolve_all(user_id)
    return EntitlementsResponse(flags=flags, source=source, disabled_globally=await entitlement_service.globally_disabled())


# --- Global feature flags (kill switch / open to all) ---

@router.get(
    "/feature-flags", response_model=list[FeatureFlagStateResponse],
    dependencies=[Depends(require_admin_permission("flags:manage"))],
)
async def list_feature_flags(db: AsyncSession = Depends(get_db)):
    """Every feature flag with its global state. A flag with no setting behaves
    normally (plan/trial/override based)."""
    settings_by_flag = await EntitlementService(db).list_global_settings()
    result = []
    for flag in FeatureFlag:
        setting = settings_by_flag.get(flag.value)
        result.append(FeatureFlagStateResponse(
            flag=flag.value,
            is_killed=bool(setting and setting.is_killed),
            is_open_to_all=bool(setting and setting.is_open_to_all),
            in_free_tier=flag in FREE_TIER_DEFAULT_FLAGS,
            note=setting.note if setting else None,
            updated_at=setting.updated_at if setting else None,
            updated_by=setting.updated_by if setting else None,
        ))
    return result


@router.put(
    "/feature-flags/{flag}", response_model=FeatureFlagStateResponse,
    dependencies=[Depends(require_admin_permission("flags:manage"))],
)
async def set_feature_flag(
    flag: FeatureFlag,
    payload: FeatureFlagUpdateRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Kill switch (`is_killed`: the feature answers 503 for everyone, immediately) and
    launch/promo switch (`is_open_to_all`: every user gets it regardless of plan; a
    per-user revocation still wins). Takes effect on all API replicas at once. Audited."""
    entitlements = EntitlementService(db)
    before = (await entitlements.list_global_settings()).get(flag.value)
    setting = await entitlements.set_global_state(
        flag=flag, actor_id=current_user.id, is_killed=payload.is_killed, is_open_to_all=payload.is_open_to_all,
        note=payload.note, note_provided="note" in payload.model_fields_set,
    )
    await AdminService(db).log(
        admin_user_id=current_user.id, action="feature_flag.update", target_type="feature_flag", target_id=flag.value,
        result=AuditResult.SUCCESS, ip_address=client_ip,
        metadata={
            "before": {"is_killed": bool(before and before.is_killed), "is_open_to_all": bool(before and before.is_open_to_all)},
            "after": {"is_killed": setting.is_killed, "is_open_to_all": setting.is_open_to_all, "note": setting.note},
        },
    )
    await db.commit()
    return FeatureFlagStateResponse(
        flag=flag.value, is_killed=setting.is_killed, is_open_to_all=setting.is_open_to_all,
        in_free_tier=flag in FREE_TIER_DEFAULT_FLAGS, note=setting.note,
        updated_at=setting.updated_at, updated_by=setting.updated_by,
    )


# --- Payments (finance view + refunds) ---

@router.get(
    "/payments", response_model=AdminPaymentListResponse,
    dependencies=[Depends(require_admin_permission("payments:view"))],
)
async def list_payments(
    status_filter: Optional[PaymentStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """All payments, newest first, optionally filtered by status. Requires
    `payments:view`."""
    rows, total = await PaymentService(db).list_all_payments(status=status_filter, limit=limit, offset=offset)
    return AdminPaymentListResponse(
        items=[AdminPaymentResponse.model_validate(p) for p in rows], total=total, limit=limit, offset=offset
    )


@router.post(
    "/payments/{payment_id}/refund", response_model=RefundResponse,
    dependencies=[
        Depends(require_admin_permission("payments:refund")),
        Depends(rate_limit(bucket="admin:payments:refund", max_requests=20, window_seconds=3600, per="user")),
    ],
)
async def refund_payment(
    payment_id: uuid.UUID,
    payload: RefundRequest,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Asks Paystack to refund a verified payment (all of the remainder, or
    `amount_minor` of it). Refunds are ASYNCHRONOUS at Paystack: this returns
    once the request is accepted (`provider_status` is normally `pending`) and
    the payment only becomes `refunded` when Paystack's signed `refund.processed`
    webhook arrives — a full refund of the payment funding the current period
    then ends that subscription automatically. Refunds issued directly in the
    Paystack dashboard are synced the same way. Requires `payments:refund`;
    every attempt (success or failure) is audit-logged."""
    result = await PaymentService(db).refund_payment(
        payment_id=payment_id, actor_id=current_user.id, amount_minor=payload.amount_minor,
        reason=payload.reason, ip_address=client_ip,
    )
    return RefundResponse(
        payment_id=result.payment_id, reference=result.reference,
        requested_amount_minor=result.requested_amount_minor, provider_status=result.provider_status,
        message="Refund requested. The payment will show as refunded once Paystack confirms it.",
    )


# --- Messaging ---

@router.post("/messages", response_model=AdminMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    message = await AdminMessagingService(db).send_message(
        actor_id=current_user.id, recipient_user_id=payload.recipient_user_id,
        message=payload.message, channel=payload.channel,
    )
    return AdminMessageResponse.model_validate(message)


# --- Broadcasts ---

@router.post("/broadcasts", response_model=BroadcastResponse, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(bucket="admin:broadcast", max_requests=5, window_seconds=3600, per="user"))])
async def create_broadcast(
    payload: CreateBroadcastRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns immediately with status=pending — the actual sending
    happens in a background worker (Blueprint §53)."""
    job = await BroadcastService(db).create_broadcast(actor_id=current_user.id, payload=payload)
    return BroadcastResponse.model_validate(job)


@router.get("/broadcasts/{broadcast_id}", response_model=BroadcastResponse)
async def get_broadcast(
    broadcast_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await BroadcastService(db).get_broadcast(actor_id=current_user.id, broadcast_id=broadcast_id)
    return BroadcastResponse.model_validate(job)


# --- Audit logs ---

@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    logs = await AdminService(db).list_audit_logs(actor_id=current_user.id, limit=limit, offset=offset)
    return [AuditLogResponse.model_validate(entry) for entry in logs]


# --- Dashboard (Blueprint §89, partial) ---

@router.get("/dashboard", response_model=DashboardStatsResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Real counts straight from the database — no placeholder
    numbers. Covers the user/subscription/trial slice of Blueprint
    §89's dashboard; see GET /admin/analytics/ai-usage for AI cost
    tracking (§57-58, now implemented)."""
    await AdminService(db).require_permission(user_id=current_user.id, permission="analytics:view")

    user_repo = UserRepository(db)
    total_users = await user_repo.count_users()
    active_users = await user_repo.count_users(status=UserStatus.ACTIVE.value)
    pending_users = await user_repo.count_users(status=UserStatus.PENDING.value)
    suspended_users = await user_repo.count_users(status=UserStatus.SUSPENDED.value)

    active_subs_result = await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)
    )
    active_trials_result = await db.execute(
        select(func.count()).select_from(UserTrial).where(UserTrial.expires_at > datetime.now(timezone.utc))
    )

    return DashboardStatsResponse(
        total_users=total_users,
        active_users=active_users,
        pending_users=pending_users,
        suspended_users=suspended_users,
        active_subscriptions=active_subs_result.scalar_one(),
        active_trials=active_trials_result.scalar_one(),
    )


# --- Security (Blueprint §62-64, §89 partial) ---

@router.post("/security/blocked-ips", status_code=status.HTTP_201_CREATED)
async def block_ip(
    ip_address: str,
    reason: str,
    duration_hours: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.security.service import SecurityService

    blocked = await SecurityService(db).block_ip(
        actor_id=current_user.id, ip_address=ip_address, reason=reason, duration_hours=duration_hours
    )
    return {
        "ip_address": blocked.ip_address, "reason": blocked.reason,
        "expires_at": blocked.expires_at.isoformat() if blocked.expires_at else None,
    }


@router.delete("/security/blocked-ips/{ip_address}", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_ip(
    ip_address: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.security.service import SecurityService

    await SecurityService(db).unblock_ip(actor_id=current_user.id, ip_address=ip_address)


@router.get("/security/blocked-ips")
async def list_blocked_ips(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.security.service import SecurityService

    blocked = await SecurityService(db).list_blocked_ips(current_user.id)
    return [
        {
            "ip_address": b.ip_address, "reason": b.reason,
            "expires_at": b.expires_at.isoformat() if b.expires_at else None,
        }
        for b in blocked
    ]


@router.get("/security/events")
async def list_security_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.security.service import SecurityService

    events = await SecurityService(db).list_events(actor_id=current_user.id, limit=limit, offset=offset)
    return [
        {
            "id": str(e.id), "user_id": str(e.user_id) if e.user_id else None, "event_type": e.event_type,
            "severity": e.severity.value, "ip_address": e.ip_address, "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


# --- Provider health (Master Prompt §78) ---

@router.get("/provider-health", dependencies=[Depends(require_admin_permission("analytics:view"))])
async def provider_health():
    """Circuit-breaker state for every external provider called by this process
    (closed/open/half_open, consecutive failures, retry-in for an open circuit),
    merged with what other processes have published to Redis."""
    from app.core.provider_health import snapshot

    return await snapshot()


# --- Analytics (Blueprint §57-58) ---

@router.get(
    "/analytics/daily", response_model=list[DailyMetricsResponse],
    dependencies=[Depends(require_admin_permission("analytics:view"))],
)
async def daily_metrics(days: int = Query(default=30, ge=1, le=366), db: AsyncSession = Depends(get_db)):
    """One row per day from the nightly aggregation job (registrations, activations, trips,
    generated itineraries, Companion turns, AI requests/tokens/cost, payments, failed logins)."""
    return [DailyMetricsResponse(date=d, metrics=m) for d, m in await MetricsService(db).series(days=days)]



@router.get("/analytics/ai-usage")
async def get_ai_usage_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Real totals from ai_usage_records — recorded on every
    Companion turn (see app/modules/companion/service.py)."""
    from app.modules.analytics.service import AnalyticsService

    return await AnalyticsService(db).get_ai_cost_summary(actor_id=current_user.id)


@router.get(
    "/analytics/ai-usage/by-user/{user_id}", response_model=AICostByUserResponse,
    dependencies=[Depends(require_admin_permission("analytics:view"))],
)
async def get_ai_usage_by_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Blueprint §58 "cost/user" — total AI spend and a per-feature
    breakdown for ONE user."""
    from app.modules.analytics.service import AnalyticsService

    return await AnalyticsService(db).get_ai_cost_by_user(actor_id=current_user.id, target_user_id=user_id)


@router.get(
    "/analytics/ai-usage/by-trip/{trip_id}", response_model=AICostByTripResponse,
    dependencies=[Depends(require_admin_permission("analytics:view"))],
)
async def get_ai_usage_by_trip(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Blueprint §58 "cost/trip" — total AI spend attributable to ONE trip
    (itinerary generation + any Companion turns held in that trip's
    conversation)."""
    from app.modules.analytics.service import AnalyticsService

    return await AnalyticsService(db).get_ai_cost_by_trip(actor_id=current_user.id, trip_id=trip_id)


@router.get(
    "/analytics/cache", response_model=CacheStatsResponse,
    dependencies=[Depends(require_admin_permission("analytics:view"))],
)
async def get_cache_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Blueprint §58/§89 "cache hit rate" and "cost savings" — per
    provider-category (geocode/currency/weather/image/route/hotel/flight/
    activity) hit rate and an order-of-magnitude estimated dollar saving
    from avoided provider calls, since the counters were last reset."""
    from app.modules.analytics.service import AnalyticsService

    return await AnalyticsService(db).get_cache_stats(actor_id=current_user.id)


@router.post(
    "/analytics/cache/reset", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_permission("analytics:view"))],
)
async def reset_cache_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Zeroes the hit/miss counters (e.g. to start a fresh reporting window
    after a deploy). Does NOT clear the underlying cached data itself."""
    from app.modules.analytics.service import AnalyticsService

    await AnalyticsService(db).reset_cache_stats(actor_id=current_user.id)


@router.get(
    "/analytics/revenue", response_model=RevenueDashboardResponse,
    dependencies=[Depends(require_admin_permission("payments:view"))],
)
async def get_revenue_dashboard(
    window_days: int = Query(default=30, ge=1, le=366),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Blueprint §57 revenue block: MRR (by plan currency, from active
    subscriptions), churn rate, trial-to-paid conversion rate, gross
    revenue from verified payments, and ESTIMATED affiliate revenue from
    booking-link click-throughs — all in one call for the admin
    dashboard's revenue tab."""
    from app.modules.analytics.service import AnalyticsService

    return await AnalyticsService(db).get_revenue_dashboard(actor_id=current_user.id, window_days=window_days)


# --- Itinerary planning limits (growth mode, long trips, monthly caps) ---

@router.get("/planning-policy", response_model=PlanningPolicyConfigResponse,
            dependencies=[Depends(require_admin_permission("plans:manage"))])
async def get_planning_policy(db: AsyncSession = Depends(get_db)):
    """The limits for itinerary generation. While `growth_mode` is on, every user gets the growth limits; switch it
    off and the premium/free limits apply by plan. Requires the `plans:manage` admin permission."""
    row = await PlanningPolicyService(db).get_row()
    await db.commit()                  # persists the default row if this database had none
    return PlanningPolicyConfigResponse.model_validate(row)


@router.put("/planning-policy", response_model=PlanningPolicyConfigResponse,
            dependencies=[Depends(require_admin_permission("plans:manage"))])
async def update_planning_policy(
    payload: PlanningPolicyUpdate,
    current_user: User = Depends(get_current_user),
    client_ip: Optional[str] = Depends(get_client_ip),
    db: AsyncSession = Depends(get_db),
):
    """Change any of the planning limits. Every change is audit-logged with what changed."""
    updates = payload.model_dump(exclude_none=True)
    before = PlanningPolicyConfigResponse.model_validate(await PlanningPolicyService(db).get_row()).model_dump()
    row = await PlanningPolicyService(db).update(updates)
    after = PlanningPolicyConfigResponse.model_validate(row).model_dump()
    await AdminService(db).log(
        admin_user_id=current_user.id, action="planning_policy.update", target_type="planning_policy",
        result=AuditResult.SUCCESS, ip_address=client_ip,
        metadata={"changed": {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}},
    )
    await db.commit()
    return PlanningPolicyConfigResponse.model_validate(row)

