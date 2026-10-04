"""PlaceService — thin wrapper over PlaceRepository for text/category/
radius search plus manual creation (see schemas.py docstring for the
temporary-open-creation caveat)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.place import Place
from app.modules.places.schemas import PlaceCreateRequest, PlaceResponse, PlaceSearchResponse
from app.repositories.place_repository import PlaceRepository


class PlaceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PlaceRepository(db)

    async def create(self, payload: PlaceCreateRequest) -> Place:
        place = Place(
            name=payload.name,
            category=payload.category,
            city=payload.city,
            country=payload.country.upper() if payload.country else None,
            latitude=payload.latitude,
            longitude=payload.longitude,
            description=payload.description,
            source="manual",
        )
        await self.repo.create(place)
        await self.db.commit()
        return place

    async def search(
        self,
        *,
        query: Optional[str] = None,
        category: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: Optional[float] = None,
    ) -> PlaceSearchResponse:
        places = await self.repo.search(
            query=query, category=category, latitude=latitude, longitude=longitude, radius_km=radius_km
        )
        results = [PlaceResponse.model_validate(p) for p in places]
        return PlaceSearchResponse(results=results, count=len(results))
