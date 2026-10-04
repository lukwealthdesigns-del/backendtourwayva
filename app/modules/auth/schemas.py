"""Pydantic request/response schemas for authentication endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.utils.passwords import validate_password_strength
from app.utils.phone import InvalidPhoneNumberError, parse_and_validate_phone
from app.utils.username import InvalidUsernameError, validate_username


class SignupRequest(BaseModel):
    """
    New signup payload per the updated requirement:
    first name + last name + username (unique) + phone number
    (validated internationally) + email + password + confirm password.

    Travel preferences and other profile fields are intentionally NOT
    part of signup — they're collected later during optional onboarding.
    """

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=3, max_length=20)
    phone_number: str = Field(
        ..., description="Full international phone number, e.g. +2348012345678"
    )
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field cannot be empty.")
        return v

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        try:
            return validate_username(v)
        except InvalidUsernameError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        try:
            parsed = parse_and_validate_phone(v)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc
        return parsed.e164

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode="after")
    def _passwords_match(self) -> "SignupRequest":
        if self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match.")
        return self


class SignupResponse(BaseModel):
    message: str
    email: EmailStr
    wayva_id: str
    otp_expiry_minutes: int


_OTP_PATTERN = r"^\d{4,8}$"


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., pattern=_OTP_PATTERN)


class ResendOTPRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(default="email_verification")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., pattern=_OTP_PATTERN)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode="after")
    def _passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_new_password:
            raise ValueError("New password and confirm password do not match.")
        return self


class UserPublic(BaseModel):
    """Safe, public-facing user representation — never includes
    password_hash or other sensitive internals."""

    id: uuid.UUID
    wayva_id: str
    username: str
    first_name: str
    last_name: str
    email: EmailStr
    is_active: bool
    country: Optional[str] = None
    currency: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    avatar_url: Optional[str] = None
    onboarding_completed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthSuccessResponse(BaseModel):
    user: UserPublic
    tokens: TokenResponse


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode="after")
    def _passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_new_password:
            raise ValueError("New password and confirm password do not match.")
        return self


class LogoutRequest(BaseModel):
    refresh_token: str


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool
    reason: Optional[str] = None


# --- Google OAuth (Sign in with Google) ---

class GoogleAuthRequest(BaseModel):
    """`id_token` is the Google ID token (credential) the frontend gets
    from Google Identity Services. It is verified server-side."""

    id_token: str = Field(..., min_length=20, max_length=4096)


class GooglePrefill(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    picture: Optional[str] = None


class GoogleAuthResponse(BaseModel):
    """status == "authenticated"     -> `user` + `tokens` are set.
    status == "profile_required"  -> a NEW Google user: the client must collect a
    unique username and a phone number and call POST /auth/google/complete with
    `signup_token`."""

    status: str
    user: Optional[UserPublic] = None
    tokens: Optional[TokenResponse] = None
    signup_token: Optional[str] = None
    signup_token_expires_in_seconds: Optional[int] = None
    prefill: Optional[GooglePrefill] = None


class GoogleCompleteRequest(BaseModel):
    signup_token: str = Field(..., min_length=20, max_length=4096)
    username: str = Field(..., min_length=3, max_length=20)
    phone_number: str = Field(..., description="Full international phone number, e.g. +2348012345678")
    # Only needed if Google did not supply a first/last name.
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        try:
            return validate_username(v)
        except InvalidUsernameError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        try:
            return parse_and_validate_phone(v).e164
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("This field cannot be empty.")
        return v
