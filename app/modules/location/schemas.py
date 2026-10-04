"""Schemas for the approximate-location endpoint."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ApproximateLocationResponse(BaseModel):
    """Where the user probably is right now — always approximate, never GPS-accurate.

    `source`: "ip" = worked out from the request's IP address; "account_country" = the IP could not
    be located (e.g. local development, VPN, provider down) so the country the account was
    localized to at signup is used. The raw IP address is never returned."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 code")
    country_name: Optional[str] = None
    timezone: Optional[str] = None
    source: Literal["ip", "account_country"]
    approximate: bool = True
