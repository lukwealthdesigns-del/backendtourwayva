"""Helpers that shape validation/HTTP errors into the standard error
envelope (Master Prompt §70) without leaking submitted values.

Pydantic/FastAPI validation errors carry an `input` field holding the
exact value the client sent — for a login/signup/reset request that is
the user's PASSWORD. It (and `ctx`, which can hold exception objects)
must never be echoed back, so errors are reduced to loc/message/type.
"""
from __future__ import annotations

from typing import Any, Iterable

_VALUE_ERROR_PREFIX = "Value error, "

_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


def sanitize_validation_errors(errors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        message = str(err.get("msg", "Invalid value."))
        if message.startswith(_VALUE_ERROR_PREFIX):
            message = message[len(_VALUE_ERROR_PREFIX):]
        cleaned.append(
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "message": message,
                "type": str(err.get("type", "value_error")),
            }
        )
    return cleaned


def http_error_code(status_code: int) -> str:
    return _HTTP_ERROR_CODES.get(status_code, f"http_{status_code}")
