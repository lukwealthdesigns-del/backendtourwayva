import pytest

from app.utils.passwords import validate_password_strength


def test_accepts_valid_password():
    assert validate_password_strength("Str0ngPassw0rd") == "Str0ngPassw0rd"


@pytest.mark.parametrize(
    "bad",
    ["short1A", "alllowercase1", "NoDigitsHere", "A1" + "x" * 80],
)
def test_rejects_weak_passwords(bad):
    with pytest.raises(ValueError):
        validate_password_strength(bad)


def test_rejects_more_than_72_bytes_even_if_fewer_characters():
    # 40 two-byte characters = 80 bytes: bcrypt would truncate / refuse it.
    with pytest.raises(ValueError):
        validate_password_strength("Aé1" + "é" * 40)
