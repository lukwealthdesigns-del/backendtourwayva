"""initial: users and otp_codes tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("wayva_id", sa.String(32), nullable=False),
        sa.Column("username", sa.String(20), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone_number_e164", sa.String(20), nullable=False),
        sa.Column("phone_country_code", sa.String(4), nullable=False),
        sa.Column("phone_region_code", sa.String(4), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column(
            "auth_provider",
            sa.Enum("email", "google", name="auth_provider_enum"),
            nullable=False,
            server_default="email",
        ),
        sa.Column("provider_subject_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "suspended", "deleted", name="user_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "user", "premium_user", "support", "moderator", "admin", "super_admin",
                name="user_role_enum",
            ),
            nullable=False,
            server_default="user",
        ),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("google_profile_picture_url", sa.String(500), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_users_wayva_id", "users", ["wayva_id"])
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_unique_constraint("uq_users_phone_number_e164", "users", ["phone_number_e164"])
    op.create_index("ix_users_wayva_id", "users", ["wayva_id"])
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_phone_number_e164", "users", ["phone_number_e164"])

    op.create_table(
        "otp_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "email_verification", "password_reset", "phone_verification", "login_2fa",
                name="otp_purpose_enum",
            ),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_otp_codes_user_id", "otp_codes", ["user_id"])
    op.create_index("ix_otp_codes_purpose", "otp_codes", ["purpose"])


def downgrade() -> None:
    op.drop_index("ix_otp_codes_purpose", table_name="otp_codes")
    op.drop_index("ix_otp_codes_user_id", table_name="otp_codes")
    op.drop_table("otp_codes")

    op.drop_index("ix_users_phone_number_e164", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_wayva_id", table_name="users")
    op.drop_table("users")

    sa.Enum(name="otp_purpose_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="auth_provider_enum").drop(op.get_bind(), checkfirst=True)
