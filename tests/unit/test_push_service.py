"""Live push: channel naming and SSE framing (pure; the Redis plumbing is not exercised here)."""
from app.services.push_frames import HEARTBEAT_FRAME, channel_for, encode_event, sse_frame


def test_each_person_has_their_own_channel():
    assert channel_for("u-1") != channel_for("u-2") and channel_for("u-1").endswith("u-1")


def test_events_are_compact_json_that_keeps_unicode():
    assert encode_event({"a": 1, "t": "Bonjour é"}) == '{"a":1,"t":"Bonjour é"}'


def test_a_frame_is_one_event_on_one_data_line():
    f = sse_frame("push", {"title": "Line one\nLine two", "body": "x"})
    assert f.startswith("event: push\ndata: ") and f.endswith("\n\n")
    assert f.count("\n") == 3                          # event line, data line, blank line: a newline in the text cannot split it


def test_string_payloads_pass_through_and_newlines_are_flattened():
    assert sse_frame("push", "a\nb") == "event: push\ndata: a b\n\n"


def test_the_heartbeat_is_an_sse_comment_that_clients_ignore():
    assert HEARTBEAT_FRAME.startswith(":") and HEARTBEAT_FRAME.endswith("\n\n")
