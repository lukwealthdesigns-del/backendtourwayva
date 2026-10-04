"""Pydantic schemas for admin endpoints (Master Blueprint §51-56)."""
from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.core.constants import (
    AdminMessageChannel,
    AdminMessageStatus,
    AuditResult,
    BroadcastSegment,
    BroadcastStatus,
    UserStatus,
)


class CreateAdminRequest(BaseModel):
    user_id: uuid.UUID
    roles: list[str] = Field(..., min_length=1, max_length=10, description="Role names, e.g. ['support_admin']")


class SetAdminRolesRequest(BaseModel):
    roles: list[str] = Field(..., min_length=1, max_length=10)


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    is_active: bool
    created_at: datetime
    roles: list[str]
    is_super: bool
    permissions: list[str] = Field(description="Effective permissions (every catalog permission for a Super Admin)")


class PermissionResponse(BaseModel):
    code: str
    description: str

    model_config = {"from_attributes": True}


class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    is_system: bool
    is_super: bool
    permissions: list[str]
    admin_count: int


class RoleCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=3, max_length=50)
    description: str = Field(default="", max_length=255)
    permissions: list[str] = Field(default_factory=list, max_length=50)


class RoleUpdateRequest(BaseModel):
    """Only the fields sent are changed. `permissions` REPLACES the role's set."""

    model_config = {"extra": "forbid"}

    description: Optional[str] = Field(default=None, max_length=255)
    permissions: Optional[list[str]] = Field(default=None, max_length=50)


class UserSummaryResponse(BaseModel):
    id: uuid.UUID
    wayva_id: str
    username: str
    email: str
    first_name: str
    last_name: str
    status: UserStatus
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    results: list[UserSummaryResponse]
    count: int
    total: int


class UserDetailResponse(UserSummaryResponse):
    country: Optional[str] = None
    currency: Optional[str] = None
    last_login_at: Optional[datetime] = None
    has_active_subscription: bool
    has_active_trial: bool


class BlockUserRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class SendMessageRequest(BaseModel):
    recipient_user_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=2000)
    channel: AdminMessageChannel = AdminMessageChannel.IN_APP


class AdminMessageResponse(BaseModel):
    id: uuid.UUID
    sender_admin_id: uuid.UUID
    recipient_user_id: uuid.UUID
    message: str
    channel: AdminMessageChannel
    status: AdminMessageStatus
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateBroadcastRequest(BaseModel):
    segment: BroadcastSegment
    message: str = Field(..., min_length=1, max_length=2000)
    channel: AdminMessageChannel = AdminMessageChannel.EMAIL


class BroadcastResponse(BaseModel):
    id: uuid.UUID
    segment: BroadcastSegment
    message: str
    channel: AdminMessageChannel
    status: BroadcastStatus
    recipient_count: Optional[int] = None
    failure_reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    admin_user_id: uuid.UUID
    action: str
    target_type: str
    target_id: Optional[str] = None
    result: AuditResult
    reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DashboardStatsResponse(BaseModel):
    total_users: int
    active_users: int
    pending_users: int
    suspended_users: int
    active_subscriptions: int
    active_trials: int


class DailyMetricsResponse(BaseModel):
    date: date_type
    metrics: dict[str, float]


class FeatureFlagStateResponse(BaseModel):
    flag: str
    is_killed: bool = Field(description="Off for EVERYONE (kill switch; beats every grant)")
    is_open_to_all: bool = Field(description="On for EVERYONE regardless of plan (a per-user revocation still wins)")
    in_free_tier: bool = Field(description="Whether the free tier includes this flag by default")
    note: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[uuid.UUID] = None


class FeatureFlagUpdateRequest(BaseModel):
    """Only the fields sent are changed. Send `note: null` to clear the note."""

    model_config = {"extra": "forbid"}

    is_killed: Optional[bool] = None
    is_open_to_all: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _something_to_change(self) -> "FeatureFlagUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one of is_killed, is_open_to_all, note.")
        return self


# --- Cost/revenue dashboard completeness (Blueprint §57-58, §89) ---

class AICostByFeatureItem(BaseModel):
    feature: str
    cost_usd: float
    requests: int


class AICostByUserResponse(BaseModel):
    user_id: uuid.UUID
    total_cost_usd: float
    total_requests: int
    total_tokens: int
    by_feature: list[AICostByFeatureItem]


class AICostByTripResponse(BaseModel):
    trip_id: uuid.UUID
    total_cost_usd: float
    total_requests: int
    by_feature: list[AICostByFeatureItem]


class CacheCategoryStats(BaseModel):
    category: str
    hits: int
    misses: int
    hit_rate: Optional[float] = None
    estimated_savings_usd: float


class CacheStatsResponse(BaseModel):
    by_category: list[CacheCategoryStats]
    total_hits: int
    total_estimated_savings_usd: float


class ChurnStatsResponse(BaseModel):
    window_days: int
    active_at_window_start: int
    cancelled_in_window: int
    churn_rate: Optional[float] = None


class TrialConversionResponse(BaseModel):
    window_days: int
    expired_trials: int
    converted: int
    conversion_rate: Optional[float] = None


class RevenueByCurrency(BaseModel):
    currency: str
    amount: float
    payment_count: int


class RevenueSummaryResponse(BaseModel):
    window_days: int
    by_currency: list[RevenueByCurrency]
    renewal_payment_count: int


class AffiliateByItemType(BaseModel):
    item_type: str
    clicks: int
    estimated_commission_usd: float


class AffiliateRevenueResponse(BaseModel):
    window_days: int
    is_estimate: bool = Field(default=True, description="Always true: Tour-Wayva has no real booking/payout relationship with Amadeus to confirm an actual commission.")
    total_estimated_commission_usd: float
    by_item_type: list[AffiliateByItemType]


class RevenueDashboardResponse(BaseModel):
    mrr_by_currency: dict[str, float]
    churn: ChurnStatsResponse
    trial_conversion: TrialConversionResponse
    revenue: RevenueSummaryResponse
    affiliate: AffiliateRevenueResponse
