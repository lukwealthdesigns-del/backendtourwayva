"""Itinerary generation worker task (Master Prompt §69: long AI workflows run
in the worker, never inside an API request)."""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="planning.generate", soft_time_limit=600, time_limit=660)
def generate_itinerary_task(job_id: str) -> None:
    asyncio.run(_run(job_id))


async def _run(job_id: str) -> None:
    from app.modules.planning.service import PlanningService

    async with AsyncSessionLocal() as db:
        # run_job records success/failure on the job itself and never raises for
        # expected failures; an unexpected crash is logged and stored too.
        await PlanningService(db).run_job(job_id)
