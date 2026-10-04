"""
Admin models (Master Blueprint §51-56).

AdminUser grants a User admin capabilities without a separate admin
identity — the same `users` row logs in the same way; AdminUser is
just a capability record layered on top (active flag + role assignments).

RBAC (Prompt §5) is database-driven:
  admin_permissions        the permissions the code checks (synced from
                           app/core/admin_permissions.py)
  admin_roles              named roles; system roles (Blueprint §55) are seeded,
                           custom roles are created by a Super Admin; `is_super`
                           marks a role that implies every permission
  admin_role_permissions   which permissions a role grants
  admin_user_roles         which roles an admin holds (an admin may hold several)

AdminAuditLog is append-only at TWO layers: its repository exposes only
`create` and `list`, and (migration 0017) a database trigger rejects any
UPDATE, DELETE or TRUNCATE — "protected from ordinary modification" (§56).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import (
    AdminMessageChannel,
    AdminMessageStatus,
    AuditResult,
    BroadcastSegment,
    BroadcastStatus,
)
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class AdminUser(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class AdminPermission(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_permissions"

    code: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class AdminRoleModel(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # System roles (Blueprint §55) cannot be deleted or renamed.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A super role implies EVERY permission and cannot be edited.
    is_super: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AdminRolePermission(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_admin_role_permission"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_permissions.id", ondelete="CASCADE"), nullable=False, index=True
    )


class AdminUserRole(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_user_roles"
    __table_args__ = (UniqueConstraint("admin_user_id", "role_id", name="uq_admin_user_role"),)

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT: a role that is still assigned cannot be deleted out from under an admin.
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class AdminAuditLog(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_audit_logs"

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    result: Mapped[AuditResult] = mapped_column(Enum(AuditResult, name="audit_result_enum", values_callable=enum_values), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AdminMessage(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "admin_messages"

    sender_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[AdminMessageChannel] = mapped_column(
        Enum(AdminMessageChannel, name="admin_message_channel_enum", values_callable=enum_values), nullable=False
    )
    status: Mapped[AdminMessageStatus] = mapped_column(
        Enum(AdminMessageStatus, name="admin_message_status_enum", values_callable=enum_values), nullable=False
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BroadcastJob(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "broadcast_jobs"

    sender_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    segment: Mapped[BroadcastSegment] = mapped_column(
        Enum(BroadcastSegment, name="broadcast_segment_enum", values_callable=enum_values), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[AdminMessageChannel] = mapped_column(
        Enum(AdminMessageChannel, name="broadcast_channel_enum", values_callable=enum_values), nullable=False
    )
    status: Mapped[BroadcastStatus] = mapped_column(
        Enum(BroadcastStatus, name="broadcast_status_enum", values_callable=enum_values), default=BroadcastStatus.PENDING, nullable=False
    )
    recipient_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
