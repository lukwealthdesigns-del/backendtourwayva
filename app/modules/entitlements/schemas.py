"""Pydantic schemas for entitlement endpoints (Master Blueprint §48)."""
from __future__ import annotations

from pydantic import BaseModel


class EntitlementsResponse(BaseModel):
    flags: dict[str, bool]
    source: str  # "override" appears per-flag in `flags`; this is the base resolution source: "trial" | "subscription" | "free"
    # Flags an admin has switched OFF for everyone (incident/maintenance): show "temporarily unavailable"
    # rather than an upgrade prompt for these.
    disabled_globally: list[str] = []


class OverrideRequest(BaseModel):
    flag: str
    is_enabled: bool
