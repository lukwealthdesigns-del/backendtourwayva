"""
Live push to a signed-in person's open app (Server-Sent Events over Redis pub/sub).

When something worth showing instantly happens (an admin message, a broadcast, a trip update) the backend PUBLISHES a small event
to that person's channel; their open app, listening on GET /notifications/stream, receives it at once and refreshes. This is a
nudge on top of the database, never the source of truth: the notification row is saved first, and publishing is best effort (if
Redis is down the person simply sees it the next time the app refreshes, as before).

The framing helpers are pure so they can be tested without Redis.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator, Iterable

from app.core.logging import get_logger
from app.services.push_frames import HEARTBEAT_FRAME, channel_for, encode_event, sse_frame

logger = get_logger(__name__)

_PIPELINE_CHUNK = 500


def _client():
    from app.services.cache_service import _get_client

    return _get_client()


async def publish(user_id: uuid.UUID | str, event: dict[str, Any]) -> None:
    """Best effort: a push must never fail or slow the action that caused it."""
    try:
        await _client().publish(channel_for(user_id), encode_event(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_publish_failed", error=str(exc))


async def publish_many(user_ids: Iterable[uuid.UUID | str], event: dict[str, Any]) -> int:
    """The same event to many people (a broadcast), pipelined in chunks. Returns how many were attempted."""
    ids = list(user_ids)
    payload = encode_event(event)
    try:
        client = _client()
        for start in range(0, len(ids), _PIPELINE_CHUNK):
            pipe = client.pipeline(transaction=False)
            for uid in ids[start:start + _PIPELINE_CHUNK]:
                pipe.publish(channel_for(uid), payload)
            await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_publish_many_failed", error=str(exc))
    return len(ids)


async def listen(user_id: uuid.UUID | str, *, max_seconds: float = 600.0, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]:
    """SSE frames for one person: 'ready', then each pushed event, with a heartbeat while quiet. Ends after `max_seconds` so the
    app reconnects (picking up a refreshed sign-in token). If Redis is unreachable it says so and ends: the app falls back to its
    normal refresh behaviour."""
    channel = channel_for(user_id)
    try:
        pubsub = _client().pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_subscribe_failed", error=str(exc))
        yield sse_frame("unavailable", {})
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_seconds
    try:
        yield sse_frame("ready", {})
        while loop.time() < deadline:
            message = await pubsub.get_message(timeout=heartbeat_seconds)
            if message is None:
                yield HEARTBEAT_FRAME
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            if isinstance(data, str) and data:
                yield sse_frame("push", data)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass
