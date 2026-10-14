"""
Structured logging configuration.

Logs enough to debug production issues without ever logging secrets,
passwords, tokens, or OTP codes in plaintext.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings
from app.core.redaction import redact_text

_SENSITIVE_KEYS = {
    "password",
    "confirm_password",
    "otp",
    "otp_code",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "authorization",
}


def _redact_sensitive(_, __, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


class RedactSecretsFilter(logging.Filter):
    """httpx logs every outgoing request line at INFO, URL and query string included, and several providers authenticate with
    `?key=...`. This scrubs credentials from EVERY standard-library log record (httpx, uvicorn, libraries) before it is printed."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record must never break logging
            return True
        scrubbed = redact_text(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        return True


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
    )
    for handler in logging.getLogger().handlers:      # on the handlers, so records from every logger pass through it
        handler.addFilter(RedactSecretsFilter())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive,
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.DEBUG else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "tourwayva"):
    return structlog.get_logger(name)
