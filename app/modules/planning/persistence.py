"""Writes a VALIDATED plan to the database as one transaction: days, items,
trip overview/status, and a new version snapshot (parent-linked, restorable)."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripItemType, TripStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.db.models.trip import TripDay, TripItem
from app.modules.itinerary.service import ItineraryService
from app.modules.planning.domain import PlannedItem, PlannedTrip
from app.modules.planning.rules import plan_total_cost
from app.repositories.trip_repository import TripRepository


def item_kwargs(item: PlannedItem, trip_day_id: uuid.UUID, sort_order: int) -> dict[str, Any]:
    return dict(
        trip_day_id=trip_day_id,
        item_type=TripItemType(item.item_type),
        title=item.title,
        description=item.description,
        location_name=item.location_name,
        latitude=item.latitude,
        longitude=item.longitude,
        start_time=item.start_time,
        end_time=item.end_time,
        estimated_cost=item.estimated_cost,
        currency=item.currency,
        provider=item.provider or ("ai" if item.source == "estimated" else None),
        source=item.source,
        external_id=item.external_id,
        booking_link=item.booking_link,
        image_url=item.image_url,
        sort_order=sort_order,
        notes=item.notes,
    )


class PlanningPersistence:
    """Callable `persist(plan, meta)` port for the generation workflow."""

    def __init__(self, db: AsyncSession, *, trip_id: uuid.UUID, actor_id: uuid.UUID, commit: bool = True,
                 day_offset: int = 0, partial: bool = False, after_save: Optional[Callable[[Any, PlannedTrip], None]] = None):
        """`commit=False` lets a caller (confirming a proposed revision) fold the
        write into its own transaction.

        Long trips: `partial=True` means the plan covers only part of the trip, starting `day_offset` days after its first
        day (plan day 1 = trip day day_offset+1). Only those days are written; the rest of the itinerary and the trip's
        overview stay as they are. `after_save(trip, plan)` runs just before the trip is saved, inside the same
        transaction, so the trip's outline bookkeeping can never disagree with the days that were written."""
        self.day_offset = day_offset
        self.partial = partial
        self.after_save = after_save
        self.db = db
        self.commit = commit
        self.trip_id = trip_id
        self.actor_id = actor_id
        self.repo = TripRepository(db)

    async def __call__(self, plan: PlannedTrip, meta: dict[str, Any]) -> dict[str, Any]:
        trip = await self.repo.get_trip(self.trip_id)
        if trip is None:
            raise NotFoundError("Trip not found.")
        # The plan was built for the trip's dates at start time; refuse to write
        # it over a trip whose dates changed while the AI was working.
        total_days = (trip.end_date - trip.start_date).days + 1
        if self.partial:
            first_expected = trip.start_date + timedelta(days=self.day_offset)
            changed = self.day_offset + len(plan.days) > total_days or plan.days[0].date != first_expected
        else:
            changed = len(plan.days) != total_days or plan.days[0].date != trip.start_date
        if changed:
            raise ConflictError("The trip dates changed while the itinerary was being generated. Please generate again.")

        existing = {d.day_number: d for d in await self.repo.list_days_for_trip(self.trip_id)}
        item_count = 0
        for planned_day in plan.days:
            day_number = planned_day.day_number + self.day_offset
            day = existing.get(day_number)
            if day is None:
                day = await self.repo.add_day(
                    TripDay(trip_id=self.trip_id, day_number=day_number, date=planned_day.date)
                )
            day.weather_summary = planned_day.weather_summary
            for old in await self.repo.list_items_for_day(day.id):
                await self.repo.delete_item(old)
            for order, planned_item in enumerate(planned_day.items):
                await self.repo.add_item(TripItem(**item_kwargs(planned_item, day.id, order)))
                item_count += 1

        if not self.partial:
            trip.overview = plan.overview or trip.overview
        if self.after_save is not None:
            self.after_save(trip, plan)
        if trip.status == TripStatus.DRAFT:
            trip.status = TripStatus.PLANNED          # never reset an ongoing/completed trip
        await self.repo.save_trip(trip)

        version = await ItineraryService(self.db)._snapshot_version(
            trip_id=self.trip_id, actor_id=self.actor_id,
            change_summary=str(meta.get("change_summary") or "AI-generated itinerary.")[:500],
        )
        if self.commit:
            await self.db.commit()

        return {
            "version_number": version.version_number,
            "version_id": str(version.id),
            "days": len(plan.days),
            "first_day": 1 + self.day_offset,
            "items": item_count,
            "currency": plan.currency,
            "total_estimated_cost": plan_total_cost(plan),
            "warnings": meta.get("warnings", []),
            "repairs": meta.get("repairs", []),
            "notes": meta.get("notes", []),
            "data_sources": meta.get("data_sources", {}),
        }
