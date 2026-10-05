"""Spoken replies: markdown becomes clean speech, length is capped at a sentence, and only the caller's own assistant
messages can be read aloud."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.core.constants import MessageRole
from app.core.exceptions import NotFoundError
from app.modules.companion import speech_service as ss


def test_markdown_is_reduced_to_speakable_text():
    md = "## Day 1\n**Visit** the *Louvre*, then [lunch](https://x.co).\n- one\n- two\n`code` ```skip```"
    out = ss.plain_speech(md, 500)
    for symbol in ("#", "*", "`", "[", "](", "http"):
        assert symbol not in out
    assert "Visit the Louvre" in out and "lunch" in out and "one" in out


def test_long_text_is_cut_at_a_sentence_boundary():
    text = "First sentence here. " * 40
    out = ss.plain_speech(text, 100)
    assert len(out) <= 100 and out.endswith(".")


def test_empty_or_symbol_only_text_gives_nothing():
    assert ss.plain_speech("", 100) == "" and ss.plain_speech("```code```", 100) == ""


class _Provider:
    content_type = "audio/mpeg"

    def __init__(self):
        self.spoken = []

    async def synthesize(self, text):
        self.spoken.append(text)
        return b"ID3audio"


def _service(message, owner_ok=True):
    svc = ss.SpeechService.__new__(ss.SpeechService)
    svc.provider = _Provider()

    class Db:
        async def get(self, model, mid):
            return message

    svc.db = Db()

    async def owned(**kw):
        if not owner_ok:
            raise NotFoundError("Conversation not found.")
        return SimpleNamespace()

    svc.companion = SimpleNamespace(get_owned_conversation=owned)
    return svc


def _msg(role=MessageRole.ASSISTANT, content="Hello **there**."):
    return SimpleNamespace(role=role, content=content, conversation_id=uuid.uuid4())


def test_speaks_an_owned_assistant_message():
    svc = _service(_msg())
    audio, ctype = asyncio.run(svc.speak_message(message_id=uuid.uuid4(), user_id=uuid.uuid4()))
    assert audio == b"ID3audio" and ctype == "audio/mpeg" and svc.provider.spoken == ["Hello there."]


@pytest.mark.parametrize("message,owner_ok", [(None, True), (_msg(role=MessageRole.USER), True), (_msg(), False)])
def test_unknown_user_or_foreign_messages_are_all_404(message, owner_ok):
    svc = _service(message, owner_ok)
    with pytest.raises(NotFoundError):
        asyncio.run(svc.speak_message(message_id=uuid.uuid4(), user_id=uuid.uuid4()))
    assert svc.provider.spoken == []
