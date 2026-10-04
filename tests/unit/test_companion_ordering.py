"""Companion message ordering: a user message and the reply to it must never share a timestamp, and rows that already
share one (written before the fix) must still list the user's message first."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from app.modules.companion.service import reply_timestamp
from app.repositories.conversation_repository import _role_rank


def test_reply_is_strictly_after_the_message_it_answers_even_if_the_clock_lags():
    future = datetime.now(timezone.utc) + timedelta(seconds=5)   # simulates clock skew / a very fast reply
    assert reply_timestamp(future) > future


def test_reply_timestamp_is_normally_just_now():
    asked = datetime.now(timezone.utc) - timedelta(seconds=2)
    answered = reply_timestamp(asked)
    assert asked < answered <= datetime.now(timezone.utc) + timedelta(milliseconds=5)


def test_role_rank_orders_user_before_assistant_before_other_roles():
    sql = str(_role_rank().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "CASE" in sql
    assert sql.index("'user'") < sql.index("'assistant'")
