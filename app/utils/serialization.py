"""JSON-safe serialization helpers (used by the user data export)."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable


def json_safe(value: Any) -> Any:
    """Convert common DB value types to JSON-serializable equivalents."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return None  # never dump raw binary into an export
    return str(value)


def row_to_dict(obj: Any, exclude: Iterable[str] = ()) -> dict[str, Any]:
    """All mapped COLUMN values of an ORM object, JSON-safe, minus `exclude`."""
    from sqlalchemy import inspect as sa_inspect

    skip = set(exclude)
    mapper = sa_inspect(obj).mapper
    return {
        attr.key: json_safe(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in skip
    }
