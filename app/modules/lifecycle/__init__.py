"""Scheduled maintenance (Master Prompt §46, §57, §69, §77): trial/subscription lifecycle
notifications and expiry, retention clean-up, daily metrics. Each piece is a plain async
service so it is testable without Celery; app/workers/maintenance_tasks.py schedules them."""
