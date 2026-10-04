"""Conversation + Message repository."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MessageRole
from app.db.models.conversation import Conversation, Message


def _role_rank():
    """Tie-breaker for messages with identical timestamps. `created_at` defaults to the database's now(), which is the
    START of the transaction, so a user message and its reply saved in one request used to share a timestamp and could
    come back in either order. New messages now get explicit timestamps (see CompanionService); this keeps rows written
    before that fix in sensible order: the user's message before the assistant's reply to it."""
    return case((Message.role == MessageRole.USER, 0), (Message.role == MessageRole.ASSISTANT, 1), else_=2)


class ConversationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def get_conversation(self, conversation_id: uuid.UUID) -> Optional[Conversation]:
        result = await self.db.execute(select(Conversation).where(Conversation.id == conversation_id))
        return result.scalar_one_or_none()

    async def list_conversations_for_user(self, user_id: uuid.UUID) -> Sequence[Conversation]:
        result = await self.db.execute(
            select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
        )
        return result.scalars().all()

    async def add_message(self, message: Message) -> Message:
        self.db.add(message)
        await self.db.flush()
        return message

    async def list_messages(self, conversation_id: uuid.UUID, *, limit: int = 20) -> Sequence[Message]:
        """Most recent `limit` messages, returned oldest-first (ready
        to feed straight into the LLM as chat history)."""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), _role_rank().desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def list_all_messages(self, conversation_id: uuid.UUID) -> Sequence[Message]:
        """Full history, oldest-first — used by summarization to
        decide what's aged out of the verbatim window."""
        result = await self.db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc(), _role_rank().asc())
        )
        return result.scalars().all()

    async def save_conversation(self, conversation: Conversation) -> Conversation:
        await self.db.flush()
        return conversation
