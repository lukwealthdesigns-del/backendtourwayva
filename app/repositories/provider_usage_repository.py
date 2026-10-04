"""Bulk-friendly writes for provider/API usage — INSERTed, never read row-by-row in the hot path."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import ApiUsageRecord, ProviderUsageRecord


class ProviderUsageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_provider_call(
        self, *, provider: str, success: bool, duration_ms: float, circuit_state: str, error_type: Optional[str]
    ) -> None:
        self.db.add(ProviderUsageRecord(
            provider=provider, success=success, duration_ms=duration_ms, circuit_state=circuit_state, error_type=error_type
        ))
        await self.db.flush()

    async def record_api_call(
        self, *, user_id: Optional[uuid.UUID], method: str, path: str, status_code: int, duration_ms: float
    ) -> None:
        self.db.add(ApiUsageRecord(user_id=user_id, method=method, path=path, status_code=status_code, duration_ms=duration_ms))
        await self.db.flush()

    async def provider_summary(self, *, since: datetime) -> Sequence[tuple[str, int, int, float]]:
        """(provider, total_calls, failed_calls, avg_duration_ms) since `since`."""
        result = await self.db.execute(
            select(
                ProviderUsageRecord.provider, func.count(),
                func.sum(case((ProviderUsageRecord.success.is_(False), 1), else_=0)),
                func.avg(ProviderUsageRecord.duration_ms),
            )
            .where(ProviderUsageRecord.created_at >= since)
            .group_by(ProviderUsageRecord.provider)
            .order_by(ProviderUsageRecord.provider)
        )
        return [(p, int(total), int(failed or 0), float(avg or 0.0)) for p, total, failed, avg in result.all()]
