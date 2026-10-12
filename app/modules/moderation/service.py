"""
ModerationService: reports about publicly shared trips. A moderator reviews a report (seeing exactly what the public sees), then
either turns the trip's shared link off or dismisses the report. Every decision is audit-logged and, when a link is turned off,
the trip's owner is told. Nothing here edits or deletes the trip itself: only the PUBLIC link is affected.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AuditResult, NotificationType
from app.core.exceptions import NotFoundError, ValidationAppError
from app.db.models.share import ContentReport, TripShareLink
from app.modules.admin.admin_service import AdminService
from app.modules.sharing import rules
from app.modules.sharing.schemas import ReportItem, ReportListResponse, ReportPreview
from app.modules.sharing.service import SharingService

_STATUSES = ("open", "actioned", "dismissed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.admin = AdminService(db)

    async def _get(self, report_id: uuid.UUID) -> ContentReport:
        report = await self.db.get(ContentReport, report_id)
        if report is None:
            raise NotFoundError("Report not found.")
        return report

    async def list_reports(self, *, status: Optional[str], limit: int, offset: int) -> ReportListResponse:
        if status is not None and status not in _STATUSES:
            raise ValidationAppError("Unknown status.")
        base = select(ContentReport)
        count = select(func.count()).select_from(ContentReport)
        if status:
            base, count = base.where(ContentReport.status == status), count.where(ContentReport.status == status)
        total = (await self.db.execute(count)).scalar_one()
        reports = (await self.db.execute(base.order_by(ContentReport.created_at.desc()).limit(limit).offset(offset))).scalars().all()

        link_ids = {r.link_id for r in reports}
        links = {l.id: l for l in (await self.db.execute(select(TripShareLink).where(TripShareLink.id.in_(link_ids)))).scalars().all()} if link_ids else {}
        trip_ids = {r.trip_id for r in reports}
        open_counts = dict((await self.db.execute(
            select(ContentReport.trip_id, func.count()).where(ContentReport.trip_id.in_(trip_ids), ContentReport.status == "open").group_by(ContentReport.trip_id)
        )).all()) if trip_ids else {}
        now = _now()
        items = [
            ReportItem(
                id=r.id, status=r.status, reason=r.reason, details=r.details, trip_id=r.trip_id, trip_title=r.trip_title,
                trip_destination=r.trip_destination,
                link_state=rules.link_state(revoked_at=links[r.link_id].revoked_at, expires_at=links[r.link_id].expires_at, now=now) if r.link_id in links else "revoked",
                open_reports_for_trip=int(open_counts.get(r.trip_id, 0)), created_at=r.created_at, resolved_at=r.resolved_at, resolution_note=r.resolution_note,
            )
            for r in reports
        ]
        return ReportListResponse(items=items, total=int(total), limit=limit, offset=offset)

    async def preview(self, *, report_id: uuid.UUID, actor_id: uuid.UUID, ip: Optional[str]) -> ReportPreview:
        """What the public sees on the reported link right now (read as the trip's owner, whitelisted fields only)."""
        report = await self._get(report_id)
        link = await self.db.get(TripShareLink, report.link_id)
        if link is None:
            return ReportPreview(link_state="revoked", trip=None)
        state = rules.link_state(revoked_at=link.revoked_at, expires_at=link.expires_at, now=_now())
        view = await SharingService(self.db).read_trip_for_link(link)
        await self.admin.log(admin_user_id=actor_id, action="moderation.preview", target_type="content_report", target_id=str(report.id), result=AuditResult.SUCCESS, ip_address=ip)
        await self.db.commit()
        return ReportPreview(link_state=state, trip=view)

    async def resolve(self, *, report_id: uuid.UUID, actor_id: uuid.UUID, action: str, note: Optional[str], ip: Optional[str]) -> ReportItem:
        report = await self._get(report_id)
        now = _now()
        note = (note or "").strip()[:500] or None
        if action == "remove_link":
            await self.db.execute(
                update(TripShareLink).where(TripShareLink.trip_id == report.trip_id, TripShareLink.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason="moderation")
            )
            await self.db.execute(
                update(ContentReport).where(ContentReport.trip_id == report.trip_id, ContentReport.status == "open")
                .values(status="actioned", resolved_by=actor_id, resolved_at=now, resolution_note=note)
            )
            await self._tell_owner(report)
        elif action == "dismiss":
            report.status, report.resolved_by, report.resolved_at, report.resolution_note = "dismissed", actor_id, now, note
        else:
            raise ValidationAppError("Unknown action.")
        await self.admin.log(
            admin_user_id=actor_id, action=f"moderation.{action}", target_type="content_report", target_id=str(report.id),
            result=AuditResult.SUCCESS, reason=note, metadata={"trip_id": str(report.trip_id)}, ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(report)
        return await self._item_for(report)

    async def _item_for(self, report: ContentReport) -> ReportItem:
        link = await self.db.get(TripShareLink, report.link_id)
        open_count = (await self.db.execute(
            select(func.count()).select_from(ContentReport).where(ContentReport.trip_id == report.trip_id, ContentReport.status == "open")
        )).scalar_one()
        state = rules.link_state(revoked_at=link.revoked_at, expires_at=link.expires_at, now=_now()) if link else "revoked"
        return ReportItem(
            id=report.id, status=report.status, reason=report.reason, details=report.details, trip_id=report.trip_id, trip_title=report.trip_title,
            trip_destination=report.trip_destination, link_state=state, open_reports_for_trip=int(open_count), created_at=report.created_at,
            resolved_at=report.resolved_at, resolution_note=report.resolution_note,
        )

    async def _tell_owner(self, report: ContentReport) -> None:
        """Best effort: the owner should know their public link was turned off, and why it matters."""
        try:
            link = await self.db.get(TripShareLink, report.link_id)
            if link is None:
                return
            from app.modules.notifications.service import NotificationService

            await NotificationService(self.db).notify(
                user_id=link.trip_owner_id, notification_type=NotificationType.ADMIN_MESSAGE,
                title="A shared link to your trip was turned off",
                body="After a report, our team turned off the public link to one of your trips. The trip itself is untouched and you can still use it.",
            )
        except Exception:  # noqa: BLE001
            pass
