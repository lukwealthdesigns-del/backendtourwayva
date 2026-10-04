import pytest

from app.utils.phone import InvalidPhoneNumberError, parse_and_validate_phone


def test_valid_nigerian_number():
    result = parse_and_validate_phone("+2348012345678")
    assert result.e164 == "+2348012345678"
    assert result.country_calling_code == "234"
    assert result.region_code == "NG"


def test_valid_us_number():
    result = parse_and_validate_phone("+14155552671")
    assert result.region_code == "US"
    assert result.country_calling_code == "1"


def test_missing_country_code_raises():
    with pytest.raises(InvalidPhoneNumberError):
        parse_and_validate_phone("08012345678")  # no + / country code, no region hint


def test_garbage_input_raises():
    with pytest.raises(InvalidPhoneNumberError):
        parse_and_validate_phone("not-a-phone-number")


def test_empty_input_raises():
    with pytest.raises(InvalidPhoneNumberError):
        parse_and_validate_phone("")
