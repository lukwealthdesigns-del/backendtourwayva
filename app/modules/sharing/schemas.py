from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CreateShareLinkRequest(BaseModel):
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365, description="Omit for a link that stays open until you turn it off")


class ShareLinkCreated(BaseModel):
    token: str                      # shown ONCE: only its hash is stored. Link = <site>/s/<token>
    path: str
    expires_at: Optional[datetime] = None
    created_at: datetime


class ShareLinkStatus(BaseModel):
    active: bool
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    view_count: int = 0
    last_viewed_at: Optional[datetime] = None


class PublicItem(BaseModel):
    item_type: str
    title: str
    description: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    image_url: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class PublicDay(BaseModel):
    day_number: int
    date: Optional[str] = None
    weather_summary: Optional[str] = None
    items: list[PublicItem]


class PublicTrip(BaseModel):
    title: str
    destination: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    travelers: Optional[int] = None
    overview: Optional[str] = None
    days: list[PublicDay]


class ReportRequest(BaseModel):
    reason: Literal["spam", "inappropriate", "private_info", "other"]
    details: Optional[str] = Field(default=None, max_length=500)


class ReportItem(BaseModel):
    id: uuid.UUID
    status: str
    reason: str
    details: Optional[str] = None
    trip_id: uuid.UUID
    trip_title: Optional[str] = None
    trip_destination: Optional[str] = None
    link_state: str
    open_reports_for_trip: int
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None


class ReportListResponse(BaseModel):
    items: list[ReportItem]
    total: int
    limit: int
    offset: int


class ReportPreview(BaseModel):
    link_state: str
    trip: Optional[PublicTrip] = None


class ResolveReportRequest(BaseModel):
    action: Literal["remove_link", "dismiss"]
    note: Optional[str] = Field(default=None, max_length=500)
