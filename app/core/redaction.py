"""Secret redaction for anything that gets logged or stored (Master Prompt §65: "Never log secrets").

httpx exception messages embed the FULL request URL — including `?key=<API KEY>` /
`?apikey=...` / `?token=...` query parameters that several of our providers use. Logging
`str(exc)` therefore wrote live API keys into the logs. Everything that describes a provider
failure goes through `redact_secrets` first. Pure stdlib.
"""
from __future__ import annotations

import re

_SECRET_QUERY_PARAM = re.compile(
    r"(?i)\b(api[_-]?key|apikey|key|access[_-]?token|token|secret|client[_-]?secret|password|sig|signature)=([^&\s'\"<>]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic|client-id)\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN = re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{8,}\b")


def redact_secrets(text: object, *, max_length: int = 300) -> str:
    """Strip credential-looking material from `text` and cap its length."""
    value = str(text)
    value = _SECRET_QUERY_PARAM.sub(lambda m: f"{m.group(1)}=***", value)
    value = _BEARER.sub(lambda m: f"{m.group(1)} ***", value)
    value = _LONG_TOKEN.sub(lambda m: f"{m.group(1)}_{m.group(2)}_***", value)
    return value if len(value) <= max_length else value[: max_length - 1] + "…"
