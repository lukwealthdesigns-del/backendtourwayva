"""
International phone number validation and normalization.

Uses Google's `phonenumbers` library (the same library behind
libphonenumber, used by Android) so numbers from every supported
country/country-code are parsed and validated correctly — not just
pattern-matched with a regex.

Callers must submit the number either:
  - in full international format, e.g. "+2348012345678", or
  - as digits with a separately-supplied ISO country hint, e.g.
    number="8012345678", region_hint="NG"

Both paths converge on a single validated E.164 string.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


class InvalidPhoneNumberError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedPhoneNumber:
    e164: str            # e.g. "+2348012345678"
    country_calling_code: str  # e.g. "234"
    region_code: str     # e.g. "NG" (ISO 3166-1 alpha-2)


def parse_and_validate_phone(raw_number: str, region_hint: Optional[str] = None) -> ParsedPhoneNumber:
    """
    Validate `raw_number` as a real, dialable phone number for any
    country and return its normalized E.164 form plus derived
    country metadata.

    Raises InvalidPhoneNumberError with a user-safe message on any
    failure (unparsable, invalid, or not a possible number).
    """
    raw_number = (raw_number or "").strip()
    if not raw_number:
        raise InvalidPhoneNumberError("Phone number is required.")

    try:
        parsed = phonenumbers.parse(raw_number, region_hint)
    except NumberParseException as exc:
        raise InvalidPhoneNumberError(
            "Could not understand this phone number. Include your country code, "
            "e.g. +2348012345678."
        ) from exc

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumberError("This phone number is not valid for its country.")

    region_code = phonenumbers.region_code_for_number(parsed) or ""
    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    country_calling_code = str(parsed.country_code)

    return ParsedPhoneNumber(
        e164=e164,
        country_calling_code=country_calling_code,
        region_code=region_code,
    )


def calling_code_for_region(region_code: Optional[str]) -> Optional[str]:
    """International dialling code (no "+") for an ISO region, e.g. "NG" -> "234"; None when unknown."""
    code = phonenumbers.country_code_for_region((region_code or "").strip().upper())
    return str(code) if code else None
