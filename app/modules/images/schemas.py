"""Pydantic schemas for image search endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ImageSearchRequest(BaseModel):
    entity: str = Field(..., min_length=2, max_length=200, description="e.g. 'Paris Eiffel Tower'")
    locale: str = Field(default="en", min_length=2, max_length=10)


class ImageResponse(BaseModel):
    url: str
    thumbnail_url: str
    width: int
    height: int
    photographer_name: str
    photographer_profile_url: str
    attribution_required: bool
    source: str  # "cache" | "live"
    provider: str
    # How well the photo matches the request: "exact" (this place), "category" (this kind of place nearby) or "area" (the neighbourhood or
    # city). Anything but "exact" is illustrative and the UI says so; a venue's name is never claimed for a photo of something else.
    match: str = "exact"
