"""Login by email or username: the request accepts either form, and the identifier classification is stable."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.auth.schemas import LoginRequest


def _is_username(identifier: str) -> bool:
    # Mirrors AuthService.login: no "@" anywhere (after an optional leading one) means a username.
    return "@" not in identifier.strip().lstrip("@") or identifier.strip().startswith("@")


def test_request_accepts_identifier_or_legacy_email_and_rejects_neither():
    assert LoginRequest(identifier=" lukman ", password="x").login_id == "lukman"
    assert LoginRequest(email="A@B.co", password="x").login_id.lower() == "a@b.co"
    assert LoginRequest(identifier="@lukman", email=None, password="x").login_id == "@lukman"
    with pytest.raises(ValidationError):
        LoginRequest(password="x")


def test_identifier_wins_when_both_are_sent():
    assert LoginRequest(identifier="lukman", email="a@b.co", password="x").login_id == "lukman"


@pytest.mark.parametrize("value,expected", [("lukman", True), ("@lukman", True), ("lukman_24", True), ("a@b.co", False), ("first.last@mail.example.org", False)])
def test_classification(value, expected):
    assert _is_username(value) is expected
