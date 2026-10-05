"""Phone change requests are normalised to E.164 and invalid numbers are rejected before any code is sent."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.users.schemas import PhoneChangeConfirm, PhoneChangeRequest


def test_valid_number_is_normalised_to_e164():
    assert PhoneChangeRequest(phone_number="+234 801 234 5678").phone_number == "+2348012345678"
    assert PhoneChangeRequest(phone_number="+1 (415) 555-2671").phone_number == "+14155552671"


@pytest.mark.parametrize("bad", ["12345", "+999999999999", "not a number", "+234 801"])
def test_invalid_numbers_are_rejected(bad):
    with pytest.raises(ValidationError):
        PhoneChangeRequest(phone_number=bad)


def test_confirm_needs_a_code_and_unknown_fields_are_rejected():
    ok = PhoneChangeConfirm(phone_number="+2348012345678", code="123456")
    assert ok.code == "123456"
    with pytest.raises(ValidationError):
        PhoneChangeConfirm(phone_number="+2348012345678", code="12")
    with pytest.raises(ValidationError):
        PhoneChangeConfirm(phone_number="+2348012345678", code="123456", is_admin=True)
