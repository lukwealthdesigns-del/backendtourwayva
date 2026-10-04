"""
User model.

Implements the updated signup requirements:
  first_name, last_name, username (unique), phone_number (E.164),
  email, password_hash, confirm_password (not stored — validated at
  the schema layer only).

Auto-derived at signup: wayva_id, country, currency, language,
timezone, google_profile_picture_url (when signing up via Google).

`is_active` starts False and is only flipped to True once the OTP
sent via Brevo is verified (see app.modules.auth.service).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import AuthProvider, UserRole, UserStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # --- Public identity ---
    wayva_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)

    # --- Personal info (collected at signup) ---
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone_number_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    phone_country_code: Mapped[str] = mapped_column(String(4), nullable=False)  # e.g. "234"
    phone_region_code: Mapped[str] = mapped_column(String(4), nullable=False)   # e.g. "NG"

    # --- Auth ---
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider_enum", values_callable=enum_values), default=AuthProvider.EMAIL, nullable=False
    )
    provider_subject_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # --- Status / activation ---
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status_enum", values_callable=enum_values), default=UserStatus.PENDING, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Role ---
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum", values_callable=enum_values), default=UserRole.USER, nullable=False
    )

    # --- Auto-derived localization (see LocalizationService) ---
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)      # ISO 3166-1 alpha-2
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)     # ISO 4217
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)    # e.g. "en"
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)    # IANA tz name

    # --- Avatar (Cloudinary-hosted, or Google-provided) ---
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    google_profile_picture_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # --- Session tracking ---
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Any access token issued (iat) before this timestamp is rejected
    # by get_current_user — see app/api/dependencies.py. This is the
    # concrete "revoke sessions" mechanism (Blueprint §51, §77):
    # JWTs are stateless, so revocation works by moving this forward
    # rather than maintaining a token blacklist.
    sessions_invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Onboarding (optional, skippable — travel preferences etc.) ---
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        # Fast lookup of "which account owns this Google subject id".
        Index("ix_users_auth_provider_subject", "auth_provider", "provider_subject_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} username={self.username} email={self.email}>"
