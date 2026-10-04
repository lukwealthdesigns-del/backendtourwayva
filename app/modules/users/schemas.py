"""User profile, preferences, onboarding, sessions and account-lifecycle schemas."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.modules.auth.schemas import UserPublic
from app.utils.username import InvalidUsernameError, validate_username

_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class MePublic(UserPublic):
    """The caller's OWN profile: the public shape plus their phone number
    and how they sign in.

    `auth_provider` alone cannot answer "is Google connected?" or "can this account change its
    password?": linking Google to an existing password account flips `auth_provider` to "google"
    while the password stays valid. So the account-settings screen should use the explicit
    flags below (build the object with `MePublic.from_user`, which fills them in):

      google_connected   a Google identity is linked to this account
      has_password       the account has a password (False = Google-only: change-password does not
                         apply; the user can set one through "Forgot password")
      sign_in_methods    the ways this account can sign in: "password" and/or "google"
    """

    phone_number_e164: str
    auth_provider: str
    google_connected: bool = False
    has_password: bool = True
    sign_in_methods: list[str] = Field(default_factory=list)

    @field_validator("auth_provider", mode="before")
    @classmethod
    def _provider_value(cls, v):
        return getattr(v, "value", v)

    @classmethod
    def from_user(cls, user) -> "MePublic":
        google_connected = bool(getattr(user, "provider_subject_id", None))
        has_password = bool(getattr(user, "password_hash", None))
        methods = (["password"] if has_password else []) + (["google"] if google_connected else [])
        return cls.model_validate(user).model_copy(update={
            "google_connected": google_connected,
            "has_password": has_password,
            "sign_in_methods": methods,
        })


class ProfileUpdateRequest(BaseModel):
    """Only the fields listed here can ever be changed through the profile
    endpoint. Anything else (email, phone, role, is_active, status, ...) is
    REJECTED (`extra="forbid"`), so a client cannot mass-assign
    privileged columns. Email and phone changes need OTP re-verification
    and are not part of this endpoint."""

    model_config = {"extra": "forbid"}

    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    username: Optional[str] = Field(default=None, min_length=3, max_length=20)
    language: Optional[str] = Field(default=None, max_length=10)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    timezone: Optional[str] = Field(default=None, max_length=64)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("This field cannot be empty.")
        return v

    @field_validator("username")
    @classmethod
    def _username(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        try:
            return validate_username(v)
        except InvalidUsernameError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("language")
    @classmethod
    def _language(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not _LANGUAGE_PATTERN.match(v):
            raise ValueError("Language must be an IETF tag such as 'en' or 'pt-BR'.")
        return v

    @field_validator("currency", "country")
    @classmethod
    def _upper_alpha(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if not v.isalpha():
            raise ValueError("Must contain letters only.")
        return v

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone. Use an IANA name such as 'Africa/Lagos'.") from exc
        return v


# --- Preferences (optional, skippable during onboarding) ---

def _clean_list(values: list[str]) -> list[str]:
    if len(values) > 20:
        raise ValueError("At most 20 items are allowed.")
    cleaned: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        if len(item) > 50:
            raise ValueError("Each item must be at most 50 characters.")
        if item.lower() not in (c.lower() for c in cleaned):
            cleaned.append(item)
    return cleaned


class PreferencesUpdateRequest(BaseModel):
    """Partial update: only the fields you send are changed (send an empty
    list / null to clear one). Sensitive preferences such as dietary and
    accessibility needs are voluntary and are stored exactly as provided."""

    model_config = {"extra": "forbid"}

    travel_styles: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    budget_preference: Optional[Literal["budget", "mid_range", "luxury"]] = None
    accommodation_preference: Optional[str] = Field(default=None, max_length=50)
    transportation_preference: Optional[str] = Field(default=None, max_length=50)
    walking_preference: Optional[Literal["low", "moderate", "high"]] = None
    dietary_preferences: Optional[list[str]] = None
    accessibility_preferences: Optional[list[str]] = None

    @field_validator("travel_styles", "interests", "dietary_preferences", "accessibility_preferences")
    @classmethod
    def _lists(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return None if v is None else _clean_list(v)


class PreferencesResponse(BaseModel):
    travel_styles: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    budget_preference: Optional[str] = None
    accommodation_preference: Optional[str] = None
    transportation_preference: Optional[str] = None
    walking_preference: Optional[str] = None
    dietary_preferences: list[str] = Field(default_factory=list)
    accessibility_preferences: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class OnboardingCompleteRequest(BaseModel):
    skipped: bool = False


# --- Sessions ---

class SessionResponse(BaseModel):
    id: uuid.UUID
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    is_current: bool = False

    model_config = {"from_attributes": True}


# --- Account deletion ---

class DeleteAccountConfirmRequest(BaseModel):
    otp_code: str = Field(..., pattern=r"^\d{4,8}$")
