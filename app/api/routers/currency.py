"""Currency endpoints (Master Blueprint §86)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user, rate_limit
from app.db.models.user import User
from app.modules.currency.schemas import ConvertRequest, ConvertResponse, RateResponse
from app.modules.currency.service import CurrencyService

router = APIRouter(prefix="/currency", tags=["Currency"])
_service = CurrencyService()


@router.get("/rates", response_model=RateResponse, dependencies=[Depends(rate_limit(bucket="currency:rates:get_rate", max_requests=60, window_seconds=300, per="user"))])
async def get_rate(
    base: str = Query(..., min_length=3, max_length=3),
    target: str = Query(..., min_length=3, max_length=3),
    current_user: User = Depends(get_current_user),
):
    return await _service.get_rate(base, target)


@router.post("/convert", response_model=ConvertResponse, dependencies=[Depends(rate_limit(bucket="currency:rates:convert", max_requests=60, window_seconds=300, per="user"))])
async def convert(payload: ConvertRequest, current_user: User = Depends(get_current_user)):
    return await _service.convert(payload.amount, payload.base, payload.target)
