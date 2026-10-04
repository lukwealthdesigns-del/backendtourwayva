"""Declarative base class and reusable model mixins."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def enum_values(enum_cls) -> list[str]:
    """Persist Python enums by VALUE (e.g. 'pending'), not by member NAME
    ('PENDING'). SQLAlchemy defaults to names; every PostgreSQL enum type
    created by the Alembic migrations is labelled with the lowercase values,
    so without this every read/write of an enum column fails against a real
    database ("invalid input value for enum ...")."""
    return [member.value for member in enum_cls]


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class UUIDPKMixin:
    """Public-facing primary key is always a UUID — never expose
    sequential internal IDs (see Master Blueprint §4)."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
