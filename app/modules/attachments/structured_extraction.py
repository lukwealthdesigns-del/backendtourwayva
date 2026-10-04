"""
Heuristic structured-field extraction (Master Blueprint §42:
"structured information" step) — regex-based pattern matching over
raw extracted text, not an ML/NLP model. Finds candidate dates,
money amounts, and confirmation/booking codes. Deliberately
conservative: returns candidates for the user to confirm (§42's own
"user confirmation" step), never treated as verified data on its own.
"""
from __future__ import annotations

import re

_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"\b(?:USD|EUR|GBP|NGN|[$€£₦])\s?\d{1,3}(?:[,.]\d{3})*(?:\.\d{2})?\b")
_CONFIRMATION_RE = re.compile(
    r"\b(?:(?i:confirmation|booking|reference|reservation))\s*(?:(?i:no\.?|number|#))?\s*[:\-]?\s*"
    r"((?=[A-Z0-9]*\d)[A-Z0-9]{5,12})\b"
)


def extract_structured_fields(text: str) -> dict[str, list[str]]:
    if not text:
        return {"dates": [], "amounts": [], "confirmation_codes": []}

    dates = list(dict.fromkeys(m.group(0) for m in _DATE_RE.finditer(text)))
    amounts = list(dict.fromkeys(m.group(0) for m in _AMOUNT_RE.finditer(text)))
    codes = list(dict.fromkeys(m.group(1) for m in _CONFIRMATION_RE.finditer(text)))

    return {"dates": dates[:10], "amounts": amounts[:10], "confirmation_codes": codes[:10]}
