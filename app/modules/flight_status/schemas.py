from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class FlightEndpoint(BaseModel):
    airport: Optional[str] = None
    iata: Optional[str] = None
    terminal: Optional[str] = None
    gate: Optional[str] = None
    scheduled: Optional[str] = None
    estimated: Optional[str] = None
    actual: Optional[str] = None
    delay_minutes: Optional[int] = None


class FlightStatusResponse(BaseModel):
    found: bool
    flight_iata: str
    airline: Optional[str] = None
    status: Optional[str] = None         # scheduled | active | landed | cancelled | incident | diverted
    flight_date: Optional[str] = None
    departure: Optional[FlightEndpoint] = None
    arrival: Optional[FlightEndpoint] = None
    provider: Optional[str] = None
    data_status: Optional[str] = None
    last_verified_at: Optional[str] = None
    source: Optional[str] = None         # "cache" | "live"
