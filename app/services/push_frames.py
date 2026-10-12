"""Pure helpers for live push (stdlib only): channel names and Server-Sent Events framing."""
from __future__ import annotations

import json
import uuid
from typing import Any

_CHANNEL_PREFIX = "tw:push:"
HEARTBEAT_FRAME = ": ping\n\n"


def channel_for(user_id: uuid.UUID | str) -> str:
    return f"{_CHANNEL_PREFIX}{user_id}"


def encode_event(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"), default=str, ensure_ascii=False)


def sse_frame(name: str, data: Any) -> str:
    """One Server-Sent Event. `data` is JSON-encoded onto a single line, so a title with a newline can never split the frame."""
    payload = data if isinstance(data, str) else encode_event(data)
    return f"event: {name}\ndata: {payload.replace(chr(10), ' ')}\n\n"
