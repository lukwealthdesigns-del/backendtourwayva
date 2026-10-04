"""
Daily metrics aggregation (Master Prompt §5 `daily_metrics`, §57, §69).

A nightly job turns raw rows into one number per day per metric so dashboards read a tiny
table instead of scanning the raw ones. Idempotent — re-running a day overwrites its rows —
and each run recomputes the last TWO complete days, so late-arriving rows are picked up.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.metrics_repository import MetricsRepository


class MetricsService:
    def __init__(self, db: AsyncSession, *, today: Callable[[], date] = lambda: datetime.now(timezone.utc).date()):
        self.db = db
        self.repo = MetricsRepository(db)
        self._today = today

    async def aggregate_day(self, day: date) -> dict[str, float]:
        values = await self.repo.compute_day(day)
        await self.repo.upsert(day, values)
        await self.db.commit()
        return values

    async def run(self) -> dict[str, int]:
        today = self._today()
        days = [today - timedelta(days=1), today - timedelta(days=2)]
        for day in days:
            await self.aggregate_day(day)
        return {"days_aggregated": len(days)}

    async def series(self, *, days: int) -> list[tuple[date, dict[str, float]]]:
        end = self._today()
        rows = await self.repo.list_range(end - timedelta(days=days - 1), end)
        grouped: dict[date, dict[str, float]] = {}
        for row in rows:
            grouped.setdefault(row.metric_date, {})[row.metric_key] = row.value
        return sorted(grouped.items())
