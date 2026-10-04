"""Welcome message: localized copy, HTML safety, optional button, and the never-raises guarantee."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.modules.notifications import welcome as w


def _user(**kw):
    base = dict(id="u1", email="a@b.co", first_name="Ada", language="en")
    base.update(kw)
    return SimpleNamespace(**base)


def test_text_is_personalised_and_localized_with_english_fallback():
    title, body = w.welcome_text(_user())
    assert title == "Welcome to TourWayva" and "Ada" in body and "{name}" not in body
    assert w.welcome_text(_user(language="fr-FR"))[0] == "Bienvenue sur TourWayva"
    assert w.welcome_text(_user(language="yo"))[0] == "Welcome to TourWayva"        # no Yoruba copy yet: English
    assert w.welcome_text(_user(language=None))[0] == "Welcome to TourWayva"
    assert "traveler" in w.welcome_text(_user(first_name=""))[1]


def test_html_escapes_user_supplied_values():
    html = w.welcome_html(_user(first_name='<script>alert(1)</script>"'))
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_button_only_for_a_real_http_url_and_rtl_for_arabic():
    assert "<a " not in w.welcome_html(_user(), "")
    assert "<a " not in w.welcome_html(_user(), "javascript:alert(1)")
    ok = w.welcome_html(_user(), "https://app.example.com/")
    assert 'href="https://app.example.com/app/home"' in ok
    assert 'dir="rtl"' in w.welcome_html(_user(language="ar"))
    assert 'dir="ltr"' in w.welcome_html(_user())


def test_every_language_has_complete_copy():
    for lang, row in w.WELCOME_COPY.items():
        assert len(row) == 5 and all(row), lang
        assert "{name}" in row[1], lang


def test_send_welcome_never_raises_even_if_everything_fails(monkeypatch):
    class Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("db down")

    import app.modules.notifications.service as svc

    monkeypatch.setattr(svc, "NotificationService", Boom)
    rolled = []

    class Db:
        async def rollback(self):
            rolled.append(1)

    asyncio.run(w.send_welcome(Db(), _user()))
    assert rolled == [1]


def test_email_failure_does_not_undo_the_notification(monkeypatch):
    calls = []

    class Svc:
        def __init__(self, db):
            self.email_provider = SimpleNamespace(send_transactional_email=self._send)

        async def notify(self, **kw):
            calls.append(("notify", kw["notification_type"].value, kw["send_email"]))

        async def _send(self, **kw):
            raise RuntimeError("brevo down")

    import app.modules.notifications.service as svc

    monkeypatch.setattr(svc, "NotificationService", Svc)
    asyncio.run(w.send_welcome(SimpleNamespace(), _user()))
    assert calls == [("notify", "system_announcement", False)]
