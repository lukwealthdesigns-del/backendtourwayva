"""
Conversation + Message models (Master Blueprint §35, §38).

Scoped for this delivery: persistence, ownership, and a direct
LLM-backed reply loop are implemented (see app/modules/companion).
NOT yet implemented: conversation summarization (§38, needed once
histories get long), the tool system (§39-40), RAG-grounded context
(§33-34, §36), and intent routing (§83) — today Companion sends
recent message history + enabled user memories directly to the LLM,
nothing more. See app/modules/companion/__init__.py for the full
gap list.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import MessageRole
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Conversation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # rolling summary of older messages — see app/modules/companion/summarization.py


class Message(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role_enum", values_callable=enum_values), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
