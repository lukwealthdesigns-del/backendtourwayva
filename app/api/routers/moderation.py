"""Moderation of publicly shared trips (admin). Requires `moderation:manage`; every decision is audit-logged."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit, require_admin_permission
from app.db.models.user import User
from app.db.session import get_db
from app.modules.moderation.service import ModerationService
from app.modules.sharing.schemas import ReportItem, ReportListResponse, ReportPreview, ResolveReportRequest

router = APIRouter(prefix="/admin/moderation", tags=["Admin: Moderation"], dependencies=[Depends(require_admin_permission("moderation:manage"))])


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    status_filter: Optional[str] = Query(default="open", alias="status", description="open | actioned | dismissed; omit for all"),
    limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await ModerationService(db).list_reports(status=status_filter, limit=limit, offset=offset)


@router.get("/reports/{report_id}/preview", response_model=ReportPreview, dependencies=[Depends(rate_limit(bucket="admin:moderation:preview", max_requests=120, window_seconds=3600, per="user"))])
async def preview_report(report_id: uuid.UUID, current_user: User = Depends(get_current_user), client_ip: Optional[str] = Depends(get_client_ip), db: AsyncSession = Depends(get_db)):
    """Exactly what the public sees on the reported link now."""
    return await ModerationService(db).preview(report_id=report_id, actor_id=current_user.id, ip=client_ip)


@router.post("/reports/{report_id}/resolve", response_model=ReportItem, dependencies=[Depends(rate_limit(bucket="admin:moderation:resolve", max_requests=120, window_seconds=3600, per="user"))])
async def resolve_report(report_id: uuid.UUID, payload: ResolveReportRequest, current_user: User = Depends(get_current_user), client_ip: Optional[str] = Depends(get_client_ip), db: AsyncSession = Depends(get_db)):
    """`remove_link` turns off every public link to that trip (the trip itself is untouched) and tells its owner; `dismiss` closes this report."""
    return await ModerationService(db).resolve(report_id=report_id, actor_id=current_user.id, action=payload.action, note=payload.note, ip=client_ip)
