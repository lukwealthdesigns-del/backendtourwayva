import pytest

from app.utils.username import InvalidUsernameError, validate_username


def test_valid_username():
    assert validate_username("lukmanibrahim") == "lukmanibrahim"


def test_uppercase_is_normalized():
    assert validate_username("LukmanIbrahim") == "lukmanibrahim"


def test_too_short_raises():
    with pytest.raises(InvalidUsernameError):
        validate_username("ab")


def test_too_long_raises():
    with pytest.raises(InvalidUsernameError):
        validate_username("a" * 21)


def test_starts_with_number_raises():
    with pytest.raises(InvalidUsernameError):
        validate_username("1lukman")


def test_consecutive_underscores_raise():
    with pytest.raises(InvalidUsernameError):
        validate_username("luk__man")


def test_reserved_username_raises():
    with pytest.raises(InvalidUsernameError):
        validate_username("admin")


def test_invalid_characters_raise():
    with pytest.raises(InvalidUsernameError):
        validate_username("lukman-ibrahim")
