"""Queued email delivery: secrets stay out of task arguments, failures fall back, retries keep the payload."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.core.exceptions import PermanentEmailError, ProviderUnavailableError
from app.providers.email import brevo_provider as brevo_module
from app.providers.email.brevo_provider import BrevoEmailProvider, _otp_email_html
from app.providers.email.factory import direct_email_provider, get_email_provider
from app.providers.email.mock_provider import MockEmailProvider
from app.providers.email.queued_provider import PAYLOAD_KEY_PREFIX, TASK_NAME, QueuedEmailProvider
from app.services.cache_service import CacheService
from app.utils.html import esc, paragraphs_html
from app.workers.email_delivery import deliver_queued_email


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# HTML safety
# ---------------------------------------------------------------------------
def test_user_supplied_text_is_escaped_before_it_reaches_an_email():
    assert esc('<a href="https://evil">Verify now</a>') == "&lt;a href=&quot;https://evil&quot;&gt;Verify now&lt;/a&gt;"
    assert esc(None) == "" and esc(42) == "42"
    html = paragraphs_html("Hello <b>x</b>\nsecond line\n\nNew paragraph")
    assert html == "<p>Hello &lt;b&gt;x&lt;/b&gt;<br>second line</p><p>New paragraph</p>"
    assert "<script" not in paragraphs_html("<script>alert(1)</script>")


def test_the_otp_email_escapes_the_recipients_name():
    html = _otp_email_html(first_name="<img src=x onerror=alert(1)>", otp_code="123456", purpose_label="email verification")
    assert "<img" not in html and "&lt;img" in html and "123456" in html


def test_the_invitation_email_cannot_carry_an_inviters_markup_to_a_third_party():
    from app.modules.collaboration.invitation_service import _invitation_email_html

    html = _invitation_email_html(inviter_name='<a href="https://evil">Click</a>', trip_title="<b>Free money</b>", role="editor")
    assert "<a href" not in html and "<b>" not in html and "&lt;a href" in html


# ---------------------------------------------------------------------------
# Brevo provider hardening
# ---------------------------------------------------------------------------
class _LogDB:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        pass


def test_an_otp_email_is_logged_without_its_code():
    db = _LogDB()
    provider = BrevoEmailProvider(db=db)
    _run(provider._log("a@b.co", "Your Tour-Wayva email verification code: 123456", "otp", success=True))
    _run(provider._log("a@b.co", "Welcome aboard", "transactional", success=True))
    assert "123456" not in db.rows[0].subject and "redacted" in db.rows[0].subject
    assert db.rows[1].subject == "Welcome aboard"


def _fake_httpx(monkeypatch, status_code):
    class StatusError(Exception):
        def __init__(self):
            self.response = NS(status_code=status_code, text="")

    class Response:
        def raise_for_status(self):
            if status_code >= 400:
                raise StatusError()

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Response()

    monkeypatch.setattr(brevo_module, "httpx", NS(AsyncClient=Client, HTTPStatusError=StatusError))
    monkeypatch.setattr(settings, "BREVO_API_KEY", "key")


@pytest.mark.parametrize("status_code,expected", [(500, ProviderUnavailableError), (503, ProviderUnavailableError),
                                                  (429, ProviderUnavailableError), (400, PermanentEmailError),
                                                  (401, PermanentEmailError), (422, PermanentEmailError)])
def test_transient_errors_are_retryable_and_permanent_rejections_are_not(monkeypatch, status_code, expected):
    _fake_httpx(monkeypatch, status_code)
    with pytest.raises(expected):
        _run(BrevoEmailProvider().send_transactional_email(to_email="a@b.co", subject="s", html_content="<p>x</p>"))


def test_a_successful_send_returns_true(monkeypatch):
    _fake_httpx(monkeypatch, 201)
    assert _run(BrevoEmailProvider().send_transactional_email(to_email="a@b.co", subject="s", html_content="x")) is True


# ---------------------------------------------------------------------------
# Queued provider
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


def _queued(redis, *, broker_up=True):
    sent = []
    direct = MockEmailProvider()

    async def enqueue(task_name, kwargs):
        if not broker_up:
            raise ConnectionError("broker down")
        sent.append((task_name, kwargs))

    return QueuedEmailProvider(direct, enqueue=enqueue), direct, sent


def test_an_otp_email_is_queued_with_only_an_opaque_key_in_the_task_arguments(redis):
    provider, direct, sent = _queued(redis)
    result = _run(provider.send_otp_email(to_email="a@b.co", first_name="Ada", otp_code="987654", purpose_label="login"))

    assert result is True and direct.sent == []                      # nothing sent inline
    (task_name, kwargs), = sent
    assert task_name == TASK_NAME and set(kwargs) == {"payload_key"}
    assert "987654" not in json.dumps(kwargs) and "a@b.co" not in json.dumps(kwargs)   # broker args carry no secret
    parked = json.loads(redis[PAYLOAD_KEY_PREFIX + kwargs["payload_key"]])
    assert parked["kind"] == "otp" and parked["kwargs"]["otp_code"] == "987654"


def test_transactional_email_is_queued_the_same_way(redis):
    provider, direct, sent = _queued(redis)
    assert _run(provider.send_transactional_email(to_email="a@b.co", subject="Hi", html_content="<p>x</p>")) is True
    parked = json.loads(redis[PAYLOAD_KEY_PREFIX + sent[0][1]["payload_key"]])
    assert parked["kind"] == "transactional" and parked["kwargs"]["subject"] == "Hi"


def test_when_the_broker_is_down_the_email_is_sent_directly_not_lost(redis, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_QUEUE_FALLBACK_INLINE", True)
    provider, direct, sent = _queued(redis, broker_up=False)
    assert _run(provider.send_otp_email(to_email="a@b.co", first_name="Ada", otp_code="111111", purpose_label="login")) is True
    assert len(direct.sent) == 1 and sent == []
    assert redis == {}                                               # the parked copy is cleaned up


def test_with_fallback_disabled_a_dead_broker_is_a_clear_error(redis, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_QUEUE_FALLBACK_INLINE", False)
    provider, direct, _ = _queued(redis, broker_up=False)
    with pytest.raises(ProviderUnavailableError):
        _run(provider.send_transactional_email(to_email="a@b.co", subject="s", html_content="x"))
    assert direct.sent == [] and redis == {}


def test_the_factory_queues_in_background_mode_and_sends_directly_inline(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")
    assert isinstance(get_email_provider(), QueuedEmailProvider)
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "inline")
    assert isinstance(get_email_provider(), BrevoEmailProvider)
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")
    assert isinstance(direct_email_provider(), BrevoEmailProvider)   # workers never queue from the queue


# ---------------------------------------------------------------------------
# Delivery in the worker
# ---------------------------------------------------------------------------
def _park(redis, kind, kwargs, key="k1"):
    redis[PAYLOAD_KEY_PREFIX + key] = json.dumps({"kind": kind, "kwargs": kwargs})
    return key


def test_delivery_sends_then_deletes_the_parked_payload(redis):
    key = _park(redis, "otp", {"to_email": "a@b.co", "first_name": "Ada", "otp_code": "123456", "purpose_label": "login"})
    provider = MockEmailProvider()
    assert _run(deliver_queued_email(key, provider)) == "sent"
    assert len(provider.sent) == 1 and PAYLOAD_KEY_PREFIX + key not in redis


def test_a_transient_failure_keeps_the_payload_so_the_retry_can_still_send_it(redis):
    key = _park(redis, "transactional", {"to_email": "a@b.co", "subject": "s", "html_content": "x", "category": "transactional"})

    class Flaky(MockEmailProvider):
        async def send_transactional_email(self, **kwargs):
            raise ProviderUnavailableError("brevo 503")

    with pytest.raises(ProviderUnavailableError):
        _run(deliver_queued_email(key, Flaky()))
    assert PAYLOAD_KEY_PREFIX + key in redis
    assert _run(deliver_queued_email(key, MockEmailProvider())) == "sent"      # the retry succeeds


def test_an_expired_payload_is_skipped_without_error(redis):
    assert _run(deliver_queued_email("gone", MockEmailProvider())) == "expired"
