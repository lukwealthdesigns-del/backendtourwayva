"""Sign-up helpers: live phone-availability check and pre-sign-up country detection."""
from __future__ import annotations

import asyncio

import pytest

from app.api.routers import auth as auth_router
from app.utils.phone import calling_code_for_region


def test_calling_code_for_region():
    assert calling_code_for_region("NG") == "234"
    assert calling_code_for_region("us") == "1"
    assert calling_code_for_region("ZZ") is None
    assert calling_code_for_region(None) is None


class _Repo:
    taken = {"+2348012345678"}

    def __init__(self, db):  # noqa: D401
        pass

    async def phone_exists(self, e164: str) -> bool:
        return e164 in self.taken


def _run(coro):
    return asyncio.run(coro)


def test_phone_available_reports_taken_free_and_invalid(monkeypatch):
    monkeypatch.setattr(auth_router, "UserRepository", _Repo)
    taken = _run(auth_router.phone_available(phone="+234 801 234 5678", db=None))
    spaced = _run(auth_router.phone_available(phone=" 2348012345678", db=None))  # "+" lost to URL decoding
    assert spaced.available is False and spaced.phone_number == "+2348012345678"
    assert taken.available is False and taken.phone_number == "+2348012345678" and "already exists" in taken.reason
    free = _run(auth_router.phone_available(phone="+2348098765432", db=None))
    assert free.available is True and free.reason is None
    bad = _run(auth_router.phone_available(phone="+234123", db=None))
    assert bad.available is False and bad.reason


class _Ip:
    def __init__(self, country):
        self.country = country

    async def lookup_country(self, ip):
        return self.country


def test_detect_region_prefers_ip_then_accept_language_then_none(monkeypatch):
    monkeypatch.setattr(auth_router, "_ipinfo", _Ip("NG"))
    res = _run(auth_router.detect_region(client_ip="8.8.8.8", accept_language="en-US"))
    assert (res.country, res.calling_code, res.source) == ("NG", "234", "ip")

    monkeypatch.setattr(auth_router, "_ipinfo", _Ip(None))
    res = _run(auth_router.detect_region(client_ip="127.0.0.1", accept_language="fr-CA,fr;q=0.9"))
    assert (res.country, res.calling_code, res.source) == ("CA", "1", "accept_language")

    res = _run(auth_router.detect_region(client_ip=None, accept_language="en"))
    assert res.country is None and res.source == "none" and res.calling_code is None
