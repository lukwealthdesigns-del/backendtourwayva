"""Trial/subscription lifecycle notices, retention, daily metrics, job locks, cache warming, beat schedule."""
from __future__ import annotations

import ast
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.core.constants import NotificationType, SubscriptionStatus
from app.modules.lifecycle import cache_warming
from app.modules.lifecycle.cache_warming import parse_pairs, warm_currency_rates
from app.modules.lifecycle.metrics import MetricsService
from app.modules.lifecycle.retention import RetentionService
from app.modules.lifecycle.service import LifecycleService, days_until, format_date, should_expire
from app.services.cache_service import CacheService
from app.workers.locks import run_exclusive

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


# ---------------------------------------------------------------------------
# Lifecycle notices
# ---------------------------------------------------------------------------
class _Notifier:
    def __init__(self):
        self.sent = []

    async def notify(self, *, user_id, notification_type, title, body, send_email=False, link=None):
        self.sent.append(NS(user_id=user_id, kind=notification_type, title=title, body=body, send_email=send_email))


class _LifecycleRepo:
    """Returns whatever the test loaded, honouring the once-only markers like the SQL does."""

    def __init__(self, trials=(), ended=(), sub_reminders=(), sub_expire=()):
        self.trials, self.ended, self.sub_reminders, self.sub_expire = trials, ended, sub_reminders, sub_expire

    async def trials_needing_reminder(self, now, window, limit):
        return [t for t in self.trials if t.reminder_sent_at is None]

    async def trials_expired_unnotified(self, now, limit):
        return [t for t in self.ended if t.expired_notified_at is None]

    async def subscriptions_needing_reminder(self, now, window, limit):
        return [s for s in self.sub_reminders if s.reminder_sent_at is None]

    async def subscriptions_to_expire(self, now, grace, limit):
        return list(self.sub_expire)


class _Monetization:
    async def get_plan(self, plan_id):
        return NS(name="Pro")


def _lifecycle(repo):
    service = LifecycleService.__new__(LifecycleService)
    service.db, service.repo, service.monetization = _FakeDB(), repo, _Monetization()
    service.notifications, service._now = _Notifier(), lambda: NOW
    return service


def _trial(days_left, **kw):
    return NS(user_id=uuid.uuid4(), expires_at=NOW + timedelta(days=days_left), reminder_sent_at=None,
              expired_notified_at=None, **kw)


def _sub(end_offset_days, *, auto_renew=False, status=SubscriptionStatus.ACTIVE):
    return NS(user_id=uuid.uuid4(), plan_id=uuid.uuid4(), status=status, auto_renew=auto_renew,
              current_period_end=NOW + timedelta(days=end_offset_days), reminder_sent_at=None, expired_notified_at=None)


def test_a_trial_reminder_is_sent_once_with_the_days_left_and_the_end_date():
    trial = _trial(2.4)
    service = _lifecycle(_LifecycleRepo(trials=[trial]))
    assert _run(service.remind_trials()) == 1
    (notice,) = service.notifications.sent
    assert notice.kind == NotificationType.TRIAL_EXPIRING and notice.send_email is True
    assert "3 days" in notice.title and format_date(trial.expires_at) in notice.body and trial.reminder_sent_at == NOW
    assert _run(service.remind_trials()) == 0                        # marker set => never reminded twice


def test_the_last_day_says_1_day_not_1_days():
    service = _lifecycle(_LifecycleRepo(trials=[_trial(0.3)]))
    _run(service.remind_trials())
    assert service.notifications.sent[0].title == "Your free trial ends in 1 day"


def test_an_ended_trial_gets_one_notice_pointing_at_the_free_plan():
    trial = _trial(-1)
    service = _lifecycle(_LifecycleRepo(ended=[trial]))
    assert _run(service.notify_ended_trials()) == 1 and trial.expired_notified_at == NOW
    assert "trial has ended" in service.notifications.sent[0].title and "free plan" in service.notifications.sent[0].body
    assert _run(service.notify_ended_trials()) == 0


def test_a_subscription_that_will_simply_end_gets_one_reminder_naming_the_plan_and_date():
    sub = _sub(2)
    service = _lifecycle(_LifecycleRepo(sub_reminders=[sub]))
    assert _run(service.remind_subscriptions()) == 1
    notice = service.notifications.sent[0]
    assert notice.kind == NotificationType.SUBSCRIPTION_EVENT and "Pro" in notice.body and format_date(sub.current_period_end) in notice.body
    assert _run(service.remind_subscriptions()) == 0


def test_an_expired_subscription_flips_to_expired_and_the_user_is_told():
    sub = _sub(-1)
    service = _lifecycle(_LifecycleRepo(sub_expire=[sub]))
    assert _run(service.expire_subscriptions()) == 1
    assert sub.status == SubscriptionStatus.EXPIRED and sub.expired_notified_at == NOW
    assert "plan has ended" in service.notifications.sent[0].title


def test_grace_protects_auto_renewing_subscriptions_but_not_one_off_ones():
    grace = timedelta(hours=24)
    late_by_2h = NOW - timedelta(hours=2)
    late_by_2d = NOW - timedelta(days=2)
    assert should_expire(auto_renew=False, period_end=late_by_2h, now=NOW, grace=grace) is True      # one-off: over
    assert should_expire(auto_renew=True, period_end=late_by_2h, now=NOW, grace=grace) is False      # renewal may be in flight
    assert should_expire(auto_renew=True, period_end=late_by_2d, now=NOW, grace=grace) is True       # grace exhausted
    assert should_expire(auto_renew=False, period_end=NOW + timedelta(hours=1), now=NOW, grace=grace) is False


def test_the_in_code_safety_net_skips_rows_the_query_returned_too_eagerly():
    renewing = _sub(0, auto_renew=True)
    renewing.current_period_end = NOW - timedelta(hours=2)            # inside the grace window
    service = _lifecycle(_LifecycleRepo(sub_expire=[renewing]))
    assert _run(service.expire_subscriptions()) == 0 and renewing.status == SubscriptionStatus.ACTIVE


def test_run_reports_every_step_and_runs_expiry_first():
    service = _lifecycle(_LifecycleRepo(trials=[_trial(2)], ended=[_trial(-1)], sub_reminders=[_sub(2)], sub_expire=[_sub(-3)]))
    assert _run(service.run()) == {"subscriptions_expired": 1, "trial_reminders": 1, "trials_ended": 1, "subscription_reminders": 1}
    assert service.notifications.sent[0].title == "Your plan has ended"


def test_date_helpers():
    assert format_date(datetime(2026, 10, 5, tzinfo=timezone.utc)) == "October 5, 2026"
    assert days_until(NOW + timedelta(hours=1), NOW) == 1 and days_until(NOW + timedelta(days=2, hours=1), NOW) == 3


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def test_retention_deletes_each_record_type_by_its_own_window_and_never_touches_the_audit_log():
    calls = {}

    class Repo:
        async def delete_finished_otps(self, *, created_before, now):
            calls["otp"] = (created_before, now)
            return 4

        async def delete_dead_sessions(self, *, before):
            calls["sessions"] = before
            return 3

        async def delete_login_attempts(self, *, before):
            calls["logins"] = before
            return 2

        async def delete_email_logs(self, *, before):
            calls["emails"] = before
            return 1

        async def delete_read_notifications(self, *, before):
            calls["notifications"] = before
            return 0

        async def delete_webhook_events(self, *, before):
            calls["webhooks"] = before
            return 5

        async def delete_provider_usage(self, *, before):
            calls["provider_usage"] = before
            return 9

        async def delete_api_usage(self, *, before):
            calls["api_usage"] = before
            return 8

        async def delete_expired_currency_cache(self, *, before):
            calls["currency_cache"] = before
            return 6

    service = RetentionService.__new__(RetentionService)
    service.db, service.repo, service._now = _FakeDB(), Repo(), lambda: NOW
    counts = _run(service.run())

    assert counts == {"otp_codes": 4, "user_sessions": 3, "login_attempts": 2, "email_logs": 1,
                      "read_notifications": 0, "webhook_events": 5, "provider_usage": 9, "api_usage": 8,
                      "currency_cache": 6}
    assert calls["otp"] == (NOW - timedelta(hours=settings.OTP_RETENTION_HOURS), NOW)
    assert calls["logins"] == NOW - timedelta(days=settings.SECURITY_LOG_RETENTION_DAYS)
    assert calls["notifications"] == NOW - timedelta(days=settings.NOTIFICATION_RETENTION_DAYS)
    assert service.db.commits == 1
    source = (Path(__file__).resolve().parents[2] / "app" / "repositories" / "retention_repository.py").read_text()
    assert "AdminAuditLog" not in source and "Payment(" not in source and "SecurityEvent" not in source


# ---------------------------------------------------------------------------
# Daily metrics
# ---------------------------------------------------------------------------
def test_daily_metrics_recompute_the_last_two_complete_days_idempotently():
    written = []

    class Repo:
        async def compute_day(self, day):
            return {"users_registered": float(day.day), "ai_cost_usd": 1.5}

        async def upsert(self, day, values):
            written.append((day, values))

        async def list_range(self, start, end):
            return [NS(metric_date=date(2026, 9, 20), metric_key="a", value=1.0),
                    NS(metric_date=date(2026, 9, 19), metric_key="b", value=2.0),
                    NS(metric_date=date(2026, 9, 19), metric_key="a", value=3.0)]

    service = MetricsService.__new__(MetricsService)
    service.db, service.repo, service._today = _FakeDB(), Repo(), lambda: date(2026, 9, 21)
    assert _run(service.run()) == {"days_aggregated": 2}
    assert [d for d, _ in written] == [date(2026, 9, 20), date(2026, 9, 19)]        # yesterday and the day before, never today
    assert service.db.commits == 2
    assert _run(service.series(days=7)) == [(date(2026, 9, 19), {"b": 2.0, "a": 3.0}), (date(2026, 9, 20), {"a": 1.0})]


# ---------------------------------------------------------------------------
# Job lock
# ---------------------------------------------------------------------------
@pytest.fixture()
def redis(monkeypatch):
    store = {}

    async def set_if_absent(key, value, ttl):
        if key in store:
            return False
        store[key] = value
        return True

    async def get_raw(key):
        return store.get(key)

    async def delete(key):
        store.pop(key, None)

    monkeypatch.setattr(CacheService, "set_if_absent", staticmethod(set_if_absent))
    monkeypatch.setattr(CacheService, "get_raw", staticmethod(get_raw))
    monkeypatch.setattr(CacheService, "delete", staticmethod(delete))
    return store


def test_a_job_runs_once_at_a_time_and_releases_only_its_own_lock(redis):
    ran = []

    async def job():
        ran.append(1)
        return "done"

    assert _run(run_exclusive("nightly", 60, job)) == "done" and ran == [1] and redis == {}

    redis["joblock:nightly"] = "someone-elses-run"
    assert _run(run_exclusive("nightly", 60, job)) is None and ran == [1]          # skipped, lock untouched
    assert redis["joblock:nightly"] == "someone-elses-run"


def test_the_lock_is_released_even_if_the_job_crashes(redis):
    async def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        _run(run_exclusive("crashy", 60, boom))
    assert redis == {}


# ---------------------------------------------------------------------------
# Cache warming
# ---------------------------------------------------------------------------
def test_pair_parsing_ignores_garbage():
    assert parse_pairs(["usd:ngn", " EUR : GBP ", "bad", "USD:USD", "TOO:LONG:X", "AB:CDE"]) == [("USD", "NGN"), ("EUR", "GBP")]


def test_warming_continues_past_a_failing_pair(monkeypatch):
    monkeypatch.setattr(settings, "CURRENCY_WARM_PAIRS", ["USD:NGN", "USD:EUR", "USD:GBP"])
    seen = []

    class FakeService:
        async def get_rate(self, base, target):
            seen.append((base, target))
            if target == "EUR":
                raise RuntimeError("provider down")

    assert _run(warm_currency_rates(FakeService())) == {"warmed": 2, "failed": 1}
    assert seen == [("USD", "NGN"), ("USD", "EUR"), ("USD", "GBP")]


# ---------------------------------------------------------------------------
# The schedule itself
# ---------------------------------------------------------------------------
WORKERS = Path(__file__).resolve().parents[2] / "app" / "workers"


def _registered_task_names():
    names = set()
    for path in WORKERS.glob("*_tasks.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "task":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        names.add(keyword.value.value)
    return names


def test_every_scheduled_task_exists():
    import re

    source = (WORKERS / "maintenance_tasks.py").read_text()
    scheduled = set(re.findall(r'"task":\s*"([\w.]+)"', source))
    assert len(scheduled) == 5, scheduled
    assert scheduled <= _registered_task_names()


def test_every_task_module_is_imported_by_the_worker():
    imports = ast.unparse(next(
        k.value for n in ast.walk(ast.parse((WORKERS / "celery_app.py").read_text()))
        if isinstance(n, ast.Call) for k in n.keywords if k.arg == "imports"
    ))
    for path in WORKERS.glob("*_tasks.py"):
        assert f"app.workers.{path.stem}" in imports, path.name
