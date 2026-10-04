"""
ItineraryService — adds/edits/removes TripItems, runs
ItineraryValidationService after every change (Blueprint §16: "Never
blindly save invalid AI output" — here, "AI output" is replaced with
"user-submitted edit", but the same discipline applies), and snapshots
a new TripVersion on every change (Blueprint §19: never destructively
overwrite).

Validation failures are returned to the caller as warnings rather
than blocking the write outright — Phase 4's repair loop (§16) will
eventually auto-correct these; until then, surfacing the conflict and
letting the user decide is the honest behavior (silently blocking
would frustrate legitimate edits; silently allowing bad data would
violate §16's own intent).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models.trip import Trip, TripDay, TripItem, TripVersion
from app.modules.itinerary.schemas import (
    ItineraryValidationResult,
    TripItemCreateRequest,
    TripItemUpdateRequest,
)
from app.modules.itinerary.snapshots import build_snapshot, snapshot_item_kwargs
from app.modules.itinerary.validation_service import ItineraryValidationService
from app.repositories.trip_repository import TripRepository

_validator = ItineraryValidationService()


class ItineraryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TripRepository(db)

    async def get_full_itinerary(self, trip_id: uuid.UUID) -> list[TripDay]:
        days = list(await self.repo.list_days_for_trip(trip_id))
        for day in days:
            day.items = list(await self.repo.list_items_for_day(day.id))  # type: ignore[attr-defined]
        return days

    async def add_item(
        self, *, day: TripDay, payload: TripItemCreateRequest, actor_id: uuid.UUID
    ) -> tuple[TripItem, ItineraryValidationResult]:
        existing_items = await self.repo.list_items_for_day(day.id)
        next_sort_order = max((i.sort_order for i in existing_items), default=-1) + 1

        item = TripItem(
            trip_day_id=day.id,
            item_type=payload.item_type,
            title=payload.title,
            description=payload.description,
            location_name=payload.location_name,
            latitude=payload.latitude,
            longitude=payload.longitude,
            start_time=payload.start_time,
            end_time=payload.end_time,
            estimated_cost=payload.estimated_cost,
            currency=payload.currency,
            provider="manual",
            source="manual",
            sort_order=next_sort_order,
            notes=payload.notes,
        )
        await self.repo.add_item(item)

        validation = await self._validate_day(day.id)
        await self._snapshot_version(
            trip_id=day.trip_id, actor_id=actor_id, change_summary=f"Added '{item.title}' to day {day.day_number}."
        )
        await self.db.commit()
        return item, validation

    async def update_item(
        self, *, item: TripItem, payload: TripItemUpdateRequest, actor_id: uuid.UUID
    ) -> tuple[TripItem, ItineraryValidationResult]:
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(item, field, value)
        await self.repo.save_item(item)

        validation = await self._validate_day(item.trip_day_id)

        day = await self.repo.get_day(item.trip_day_id)
        await self._snapshot_version(
            trip_id=day.trip_id, actor_id=actor_id, change_summary=f"Updated '{item.title}'."
        )
        await self.db.commit()
        return item, validation

    async def delete_item(self, *, item: TripItem, actor_id: uuid.UUID) -> None:
        day = await self.repo.get_day(item.trip_day_id)
        title = item.title
        await self.repo.delete_item(item)

        if day is not None:
            await self._snapshot_version(
                trip_id=day.trip_id, actor_id=actor_id, change_summary=f"Removed '{title}'."
            )
        await self.db.commit()

    async def _validate_day(self, day_id: uuid.UUID) -> ItineraryValidationResult:
        day = await self.repo.get_day(day_id)
        if day is None:
            raise NotFoundError("Trip day not found.")
        items = list(await self.repo.list_items_for_day(day_id))
        return _validator.validate_day(day, items)

    # --- Versioning (Blueprint §19) ---
    async def _snapshot_version(self, *, trip_id: uuid.UUID, actor_id: uuid.UUID, change_summary: str) -> TripVersion:
        """Append a COMPLETE snapshot as a new version whose parent is the
        previous latest version (Blueprint §19: versions form a chain and are
        never overwritten)."""
        trip = await self.repo.get_trip(trip_id)
        days = await self.repo.list_days_for_trip(trip_id)
        days_with_items = [(d, list(await self.repo.list_items_for_day(d.id))) for d in days]

        existing = await self.repo.list_versions(trip_id)          # newest first
        parent_id = existing[0].id if existing else None

        next_version_number = trip.current_version_number + 1
        version = TripVersion(
            trip_id=trip_id,
            version_number=next_version_number,
            created_by=actor_id,
            change_summary=change_summary,
            snapshot=build_snapshot(trip.overview, days_with_items),
            parent_version_id=parent_id,
        )
        await self.repo.add_version(version)

        trip.current_version_number = next_version_number
        await self.repo.save_trip(trip)
        return version

    async def create_checkpoint(self, *, trip_id: uuid.UUID, actor_id: uuid.UUID, label: str) -> TripVersion:
        """Save the CURRENT itinerary as a named version (non-destructive: nothing
        is changed). Authorization is the caller's job."""
        version = await self._snapshot_version(
            trip_id=trip_id, actor_id=actor_id, change_summary=f"Checkpoint: {label.strip()[:400]}"
        )
        await self.db.commit()
        return version

    async def list_versions(self, trip_id: uuid.UUID):
        return await self.repo.list_versions(trip_id)

    async def restore_version(self, *, trip: Trip, version_id: uuid.UUID, actor_id: uuid.UUID) -> TripVersion:
        """Replace current days/items with a snapshot's contents, then
        record the restoration itself as a NEW version — history is
        append-only, never rewritten in place (Blueprint §19)."""
        version = await self.repo.get_version(version_id)
        if version is None or version.trip_id != trip.id:
            raise NotFoundError("Trip version not found.")

        days = await self.repo.list_days_for_trip(trip.id)
        days_by_number = {d.day_number: d for d in days}

        for day_snapshot in version.snapshot.get("days", []):
            day = days_by_number.get(day_snapshot["day_number"])
            if day is None:
                continue
            day.weather_summary = day_snapshot.get("weather_summary")
            for item in await self.repo.list_items_for_day(day.id):
                await self.repo.delete_item(item)
            for item_snapshot in day_snapshot.get("items", []):
                await self.repo.add_item(TripItem(**snapshot_item_kwargs(item_snapshot, day.id)))

        if "overview" in version.snapshot:
            trip.overview = version.snapshot.get("overview")
            await self.repo.save_trip(trip)

        new_version = await self._snapshot_version(
            trip_id=trip.id, actor_id=actor_id,
            change_summary=f"Restored to version {version.version_number}.",
        )
        await self.db.commit()
        return new_version
