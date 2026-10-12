"""
SharingService: public, read-only trip links, and reports about them.

Owner side (signed in, trip owner only): create a link (replaces the previous one), see its status, turn it off.
Public side (no sign-in): read the shared trip through the link, or report it. A public read runs as the trip's owner (row-level
security) and returns ONLY the whitelisted public view (see rules.public_trip): never the budget, costs, notes or booking links.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.db.models.share import ContentReport, TripShareLink
from app.db.models.user import User
from app.db.rls import set_rls_user
from app.modules.guest.rules import hash_ip
from app.modules.sharing import rules
from app.modules.sharing.schemas import ShareLinkCreated, ShareLinkStatus
from app.modules.trips.service import TripService
from app.repositories.trip_repository import TripRepository

_UNAVAILABLE = "This link isn't available. It may have been turned off or may have expired."


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SharingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = TripRepository(db)

    # ------------------------------ owner side ------------------------------
    async def _owned_trip(self, trip_id: uuid.UUID, user: User):
        trip = await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user.id)
        if trip.owner_id != user.id:
            raise ForbiddenError("Only the owner of a trip can share it with a public link.")
        return trip

    async def create_link(self, *, trip_id: uuid.UUID, user: User, expires_in_days: Optional[int]) -> ShareLinkCreated:
        trip = await self._owned_trip(trip_id, user)
        days = await self.repo.list_days_for_trip(trip.id)
        if not any(list(await self.repo.list_items_for_day(d.id)) for d in days):
            raise ValidationAppError("Generate the itinerary before sharing it.")
        now = _now()
        # One live link per trip: a new link replaces the old one, so a forgotten link can never outlive its replacement.
        await self.db.execute(
            update(TripShareLink).where(TripShareLink.trip_id == trip.id, TripShareLink.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason="replaced")
        )
        token = rules.new_token()
        link = TripShareLink(
            trip_id=trip.id, trip_owner_id=trip.owner_id, token_hash=rules.hash_token(token), created_by=user.id,
            expires_at=rules.expiry_from(now, expires_in_days),
        )
        self.db.add(link)
        await self.db.commit()
        return ShareLinkCreated(token=token, path=f"/s/{token}", expires_at=link.expires_at, created_at=link.created_at)

    async def _live_link(self, trip_id: uuid.UUID) -> Optional[TripShareLink]:
        rows = (await self.db.execute(
            select(TripShareLink).where(TripShareLink.trip_id == trip_id, TripShareLink.revoked_at.is_(None)).order_by(TripShareLink.created_at.desc())
        )).scalars().all()
        now = _now()
        for link in rows:
            if rules.link_state(revoked_at=link.revoked_at, expires_at=link.expires_at, now=now) == "active":
                return link
        return None

    async def status(self, *, trip_id: uuid.UUID, user: User) -> ShareLinkStatus:
        trip = await self._owned_trip(trip_id, user)
        link = await self._live_link(trip.id)
        if link is None:
            return ShareLinkStatus(active=False)
        return ShareLinkStatus(active=True, created_at=link.created_at, expires_at=link.expires_at, view_count=link.view_count, last_viewed_at=link.last_viewed_at)

    async def revoke(self, *, trip_id: uuid.UUID, user: User) -> int:
        trip = await self._owned_trip(trip_id, user)
        result = await self.db.execute(
            update(TripShareLink).where(TripShareLink.trip_id == trip.id, TripShareLink.revoked_at.is_(None))
            .values(revoked_at=_now(), revoked_reason="owner")
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    # ------------------------------ public side ------------------------------
    async def _link_by_token(self, token: Optional[str]) -> TripShareLink:
        if not rules.looks_like_token(token):
            raise NotFoundError(_UNAVAILABLE)
        link = (await self.db.execute(select(TripShareLink).where(TripShareLink.token_hash == rules.hash_token(token)))).scalar_one_or_none()
        if link is None or rules.link_state(revoked_at=link.revoked_at, expires_at=link.expires_at, now=_now()) != "active":
            raise NotFoundError(_UNAVAILABLE)      # unknown, turned off and expired all look the same from outside
        return link

    async def read_trip_for_link(self, link: TripShareLink) -> Optional[dict[str, Any]]:
        """The whitelisted public view of the link's trip, read as the trip's owner. None if the trip no longer exists."""
        await set_rls_user(self.db, link.trip_owner_id)
        trip = await self.repo.get_trip(link.trip_id)
        if trip is None:
            return None
        days = await self.repo.list_days_for_trip(trip.id)
        return rules.public_trip(trip, [(d, list(await self.repo.list_items_for_day(d.id))) for d in days])

    async def public_view(self, token: Optional[str]) -> dict[str, Any]:
        link = await self._link_by_token(token)
        view = await self.read_trip_for_link(link)
        if view is None:
            raise NotFoundError(_UNAVAILABLE)
        await self.db.execute(
            update(TripShareLink).where(TripShareLink.id == link.id)
            .values(view_count=TripShareLink.view_count + 1, last_viewed_at=_now())
        )
        await self.db.commit()
        return view

    async def report(self, *, token: Optional[str], reason: str, details: Optional[str], client_ip: Optional[str]) -> None:
        link = await self._link_by_token(token)
        iph = hash_ip(client_ip, settings.SECRET_KEY) if client_ip else None
        if iph:
            already = (await self.db.execute(
                select(func.count()).select_from(ContentReport).where(
                    ContentReport.link_id == link.id, ContentReport.reporter_ip_hash == iph, ContentReport.status == "open"
                )
            )).scalar_one()
            if already:
                return                                 # the same reader reporting the same open link again: nothing to add
        await set_rls_user(self.db, link.trip_owner_id)
        trip = await self.repo.get_trip(link.trip_id)
        self.db.add(ContentReport(
            link_id=link.id, trip_id=link.trip_id, trip_title=(trip.title if trip else None), trip_destination=(trip.destination if trip else None),
            reason=reason, details=(details or "").strip()[:500] or None, reporter_ip_hash=iph, status="open",
        ))
        await self.db.commit()
