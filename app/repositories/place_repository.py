"""Place repository — text search and coordinate bounding-box search.

Bounding-box search (not true radius/haversine) is a deliberate
simplification: it's index-friendly with plain PostgreSQL and good
enough for "places near here" at city scale. A precise
haversine-distance or PostGIS-backed radius query is a reasonable
Phase 8 hardening upgrade once real usage data shows it's needed.
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.place import Place

_KM_PER_DEGREE_LAT = 111.0


class PlaceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, place: Place) -> Place:
        self.db.add(place)
        await self.db.flush()
        return place

    async def get_by_id(self, place_id: uuid.UUID) -> Optional[Place]:
        result = await self.db.execute(select(Place).where(Place.id == place_id))
        return result.scalar_one_or_none()

    async def search(
        self,
        *,
        query: Optional[str] = None,
        category: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: Optional[float] = None,
        limit: int = 20,
    ) -> Sequence[Place]:
        stmt = select(Place)

        if query:
            stmt = stmt.where(Place.name.ilike(f"%{query}%"))

        if category:
            stmt = stmt.where(Place.category == category)

        if latitude is not None and longitude is not None and radius_km:
            lat_delta = radius_km / _KM_PER_DEGREE_LAT
            # Longitude degrees shrink toward the poles — approximate
            # correction using the latitude's cosine.
            import math

            lon_delta = radius_km / (_KM_PER_DEGREE_LAT * max(math.cos(math.radians(latitude)), 0.01))
            stmt = stmt.where(
                Place.latitude.between(latitude - lat_delta, latitude + lat_delta),
                Place.longitude.between(longitude - lon_delta, longitude + lon_delta),
            )

        stmt = stmt.limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()
