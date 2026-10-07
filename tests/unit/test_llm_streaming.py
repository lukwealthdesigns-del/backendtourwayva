"""Streaming replies: the accumulator folds OpenAI chunks correctly (text, tool calls split across chunks, usage), tells
the user to discard a preamble that turned into a tool call, and providers that cannot stream still work."""
from __future__ import annotations

import asyncio
import json

from app.api.routers.companion import _sse
from app.providers.llm.interface import LLMProvider, LLMResponse, ToolCall
from app.providers.llm.openai_provider import StreamAccumulator


def _chunk(content=None, tool_calls=None, usage=None, model="gpt-x"):
    delta = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"delta": delta}] if delta else [], **({"usage": usage} if usage else {})}


def test_text_is_forwarded_in_order_and_aggregated_with_usage():
    acc = StreamAccumulator("fallback-model")
    sent = []
    for c in [_chunk("Hel"), _chunk("lo "), _chunk("world"), _chunk(usage={"prompt_tokens": 11, "completion_tokens": 3})]:
        sent += acc.feed(c)
    out = acc.result(used_fallback=False)
    assert sent == ["Hel", "lo ", "world"]
    assert out.content == "Hello world" and out.model_used == "gpt-x" and not out.tool_calls
    assert (out.prompt_tokens, out.completion_tokens) == (11, 3)


def test_tool_call_arguments_split_across_chunks_are_reassembled():
    acc = StreamAccumulator("m")
    acc.feed(_chunk(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": '{"la'}}]))
    acc.feed(_chunk(tool_calls=[{"index": 0, "function": {"arguments": 't": 6.5, "lng": 3.4}'}}]))
    acc.feed(_chunk(tool_calls=[{"index": 1, "id": "call_2", "function": {"name": "convert_currency", "arguments": "{}"}}]))
    out = acc.result(used_fallback=True)
    assert [c.name for c in out.tool_calls] == ["get_weather", "convert_currency"]
    assert out.tool_calls[0].arguments == {"lat": 6.5, "lng": 3.4} and out.tool_calls[0].id == "call_1"
    assert out.used_fallback is True


def test_a_preamble_followed_by_a_tool_call_is_reset_and_later_text_is_held_back():
    acc = StreamAccumulator("m")
    sent = acc.feed(_chunk("Let me check"))
    sent += acc.feed(_chunk(tool_calls=[{"index": 0, "id": "c", "function": {"name": "get_trip", "arguments": "{}"}}]))
    sent += acc.feed(_chunk(" the trip"))          # arrives after the tool call started: not shown
    assert sent == ["Let me check", None]
    out = acc.result(used_fallback=False)
    assert out.content == "Let me check the trip" and out.tool_calls[0].name == "get_trip"


def test_a_tool_call_with_no_preamble_never_resets():
    acc = StreamAccumulator("m")
    assert acc.feed(_chunk(tool_calls=[{"index": 0, "id": "c", "function": {"name": "x", "arguments": "{}"}}])) == []


def test_bad_tool_arguments_become_empty_not_a_crash():
    acc = StreamAccumulator("m")
    acc.feed(_chunk(tool_calls=[{"index": 0, "id": "c", "function": {"name": "x", "arguments": "{not json"}}]))
    assert acc.result(used_fallback=False).tool_calls[0].arguments == {}


class _NoStreamProvider(LLMProvider):
    def __init__(self, response):
        self.response = response

    async def generate(self, messages, **kw):
        return self.response


def _resp(content="", calls=()):
    return LLMResponse(content=content, model_used="m", prompt_tokens=1, completion_tokens=1, provider="mock", tool_calls=list(calls))


def test_providers_without_streaming_forward_the_finished_answer_once():
    got = []

    async def on_delta(t):
        got.append(t)

    out = asyncio.run(_NoStreamProvider(_resp("All done.")).generate_stream([], on_delta=on_delta))
    assert got == ["All done."] and out.content == "All done."


def test_default_stream_forwards_nothing_when_the_model_asked_for_a_tool():
    got = []

    async def on_delta(t):
        got.append(t)

    asyncio.run(_NoStreamProvider(_resp("", [ToolCall("1", "get_trip", {})])).generate_stream([], on_delta=on_delta))
    assert got == []


def test_sse_frame_format():
    frame = _sse("delta", {"text": "héllo\nworld"})
    assert frame == 'event: delta\ndata: {"text": "h\\u00e9llo\\nworld"}\n\n'
    assert json.loads(frame.split("data: ")[1].strip())["text"] == "héllo\nworld"
