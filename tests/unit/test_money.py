import pytest

from app.utils.money import from_minor_units, to_minor_units


def test_converts_to_minor_units_without_float_errors():
    assert to_minor_units(19.99) == 1999          # int(19.99 * 100) would give 1998
    assert to_minor_units(5000) == 500000
    assert to_minor_units("0.10") + to_minor_units("0.20") == 30
    assert to_minor_units(1.005) == 101           # half-up


def test_rejects_zero_and_negative():
    for bad in (0, -5, "0.00"):
        with pytest.raises(ValueError):
            to_minor_units(bad)


def test_from_minor_units():
    assert from_minor_units(1999) == 19.99
    assert from_minor_units(500000) == 5000.0
