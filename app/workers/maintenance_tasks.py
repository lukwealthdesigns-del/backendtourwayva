"""
Scheduled maintenance tasks (Celery beat) — Master Prompt §46, §57, §69, §77.

  maintenance.lifecycle      hourly   trial/subscription reminders and expiry
  payments.reconcile         10 min   verify PENDING payments whose webhook never arrived
  maintenance.retention      daily    delete expired personal/operational records
  analytics.daily_metrics    daily    aggregate yesterday (and the day before) into daily_metrics
  cache.warm_currency        6 hours  refresh common exchange-rate pairs

Each task takes a Redis lock (no overlapping runs) and the work itself is idempotent.
The logic lives in plain async services; these functions only open a session and call them.
"""
from __future__ import annotations

import asyncio
from typing import Any

from celery.schedules import crontab

from app.db.session import AsyncSessionLocal
from app.workers.celery_app import celery_app
from app.workers.locks import run_exclusive

BEAT_SCHEDULE = {
    "lifecycle-hourly": {"task": "maintenance.lifecycle", "schedule": crontab(minute=5)},
    "payments-reconcile": {"task": "payments.reconcile", "schedule": crontab(minute="*/10")},
    "retention-daily": {"task": "maintenance.retention", "schedule": crontab(hour=3, minute=15)},
    "daily-metrics": {"task": "analytics.daily_metrics", "schedule": crontab(hour=0, minute=40)},
    "currency-warm": {"task": "cache.warm_currency", "schedule": crontab(hour="*/6", minute=25)},
}
celery_app.conf.beat_schedule = BEAT_SCHEDULE


def _run(name: str, ttl: int, job) -> Any:
    return asyncio.run(run_exclusive(name, ttl, job))


@celery_app.task(name="maintenance.lifecycle", ignore_result=True)
def lifecycle_task() -> Any:
    async def job():
        from app.modules.lifecycle.service import LifecycleService

        async with AsyncSessionLocal() as db:
            return await LifecycleService(db).run()

    return _run("lifecycle", 1800, job)


@celery_app.task(name="payments.reconcile", ignore_result=True)
def reconcile_payments_task() -> Any:
    async def job():
        from app.modules.payments.service import PaymentService

        async with AsyncSessionLocal() as db:
            return await PaymentService(db).reconcile_pending()

    return _run("payments-reconcile", 600, job)


@celery_app.task(name="maintenance.retention", ignore_result=True)
def retention_task() -> Any:
    async def job():
        from app.modules.lifecycle.retention import RetentionService

        async with AsyncSessionLocal() as db:
            return await RetentionService(db).run()

    return _run("retention", 3600, job)


@celery_app.task(name="analytics.daily_metrics", ignore_result=True)
def daily_metrics_task() -> Any:
    async def job():
        from app.modules.lifecycle.metrics import MetricsService

        async with AsyncSessionLocal() as db:
            return await MetricsService(db).run()

    return _run("daily-metrics", 3600, job)


@celery_app.task(name="cache.warm_currency", ignore_result=True)
def warm_currency_task() -> Any:
    async def job():
        from app.modules.lifecycle.cache_warming import warm_currency_rates

        return await warm_currency_rates()

    return _run("currency-warm", 900, job)
