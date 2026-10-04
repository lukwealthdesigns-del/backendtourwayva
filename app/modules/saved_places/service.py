"""
SavedPlaceService — create/list/delete a user's saved places.

Save is idempotent by (user, name, ~coordinates): asking to save the same place twice
returns the existing row rather than creating a duplicate (`was_new=False`), which matters
for the Companion tool — the model may plausibly call `save_place` more than once for the
same request.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models.saved_place import SavedPlace
from app.modules.saved_places.schemas import SavePlaceRequest
from app.modules.trips.service import TripService
from app.repositories.saved_place_repository import SavedPlaceRepository


@dataclass
class SaveResult:
    place: SavedPlace
    was_new: bool


class SavedPlaceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SavedPlaceRepository(db)

    async def save(self, *, user_id: uuid.UUID, payload: SavePlaceRequest) -> SaveResult:
        if payload.trip_id is not None:
            # The trip must be the user's own (owner or member) — but a saved place is
            # personal, so no editor requirement, and losing access to the trip later never
            # deletes the place (the FK is ON DELETE SET NULL).
            await TripService(self.db).get_trip_authorized(trip_id=payload.trip_id, user_id=user_id)

        existing = await self.repo.find_duplicate(user_id, name=payload.name, latitude=payload.latitude, longitude=payload.longitude)
        if existing is not None:
            return SaveResult(place=existing, was_new=False)

        place = await self.repo.create(SavedPlace(
            user_id=user_id, name=payload.name, category=payload.category, city=payload.city,
            country=payload.country.upper() if payload.country else None, latitude=payload.latitude,
            longitude=payload.longitude, notes=payload.notes, source=payload.source,
            external_ref=payload.external_ref, trip_id=payload.trip_id,
        ))
        await self.db.commit()
        return SaveResult(place=place, was_new=True)

    async def list_for_user(self, user_id: uuid.UUID, *, trip_id: Optional[uuid.UUID] = None) -> list[SavedPlace]:
        return list(await self.repo.list_for_user(user_id, trip_id=trip_id))

    async def delete(self, *, user_id: uuid.UUID, place_id: uuid.UUID) -> None:
        place = await self.repo.get(place_id)
        if place is None:
            raise NotFoundError("Saved place not found.")
        if place.user_id != user_id:
            raise ForbiddenError("You do not have access to this saved place.")
        await self.repo.delete(place)
        await self.db.commit()
