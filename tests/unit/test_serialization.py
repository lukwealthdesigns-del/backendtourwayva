import datetime as dt
import uuid
from decimal import Decimal
from enum import Enum

from app.utils.serialization import json_safe


class _Color(str, Enum):
    RED = "red"


def test_json_safe_converts_db_types():
    uid = uuid.uuid4()
    now = dt.datetime(2026, 9, 19, 12, 0, tzinfo=dt.timezone.utc)
    out = json_safe({"id": uid, "at": now, "d": dt.date(2026, 9, 19), "c": _Color.RED, "n": Decimal("1.5"), "raw": b"xx"})
    assert out == {"id": str(uid), "at": now.isoformat(), "d": "2026-09-19", "c": "red", "n": 1.5, "raw": None}


def test_json_safe_recurses_into_collections():
    assert json_safe([{"a": (1, 2)}, {3}]) == [{"a": [1, 2]}, [3]]
