"""Pydantic schemas for currency endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RateQuery(BaseModel):
    base: str = Field(..., min_length=3, max_length=3)
    target: str = Field(..., min_length=3, max_length=3)


class RateResponse(BaseModel):
    base: str
    target: str
    rate: float
    fetched_at: datetime
    source: str  # "cache" | "live"
    provider: str


class ConvertRequest(BaseModel):
    amount: float = Field(..., gt=0)
    base: str = Field(..., min_length=3, max_length=3)
    target: str = Field(..., min_length=3, max_length=3)


class ConvertResponse(BaseModel):
    original_amount: float
    original_currency: str
    converted_amount: float
    converted_currency: str
    rate: float
    fetched_at: datetime
    source: str
    provider: str
