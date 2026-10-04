"""Database-driven admin RBAC + append-only audit log

  * admin_permissions, admin_roles, admin_role_permissions, admin_user_roles
  * seeds the permission catalog and the Blueprint §55 system roles
  * migrates each admin's single `admin_users.role` into admin_user_roles, then
    drops that column and its enum type
  * a trigger that rejects UPDATE / DELETE / TRUNCATE on admin_audit_logs

Revision ID: 0017_admin_rbac
Revises: 0016_pending_change_revise
Create Date: 2026-09-21

The seed data below is a SNAPSHOT (migrations must not import application code
that will keep changing). Later catalog additions are applied by
`sync_rbac_catalog` (run at startup / `python -m scripts.sync_rbac`).
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_admin_rbac"
down_revision: Union[str, None] = "0016_pending_change_revise"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = {
    "users:view": "View and search user accounts",
    "users:block": "Block, unblock and suspend users",
    "users:delete": "Delete user accounts",
    "users:manage_sessions": "Revoke a user's sessions",
    "messaging:send": "Send direct messages to users",
    "broadcast:send": "Send broadcast messages to user segments",
    "audit:view": "View the admin audit log",
    "analytics:view": "View analytics and AI cost dashboards",
    "content:manage": "Manage shared travel content (places, knowledge base)",
    "plans:manage": "Manage plans, trials and per-user feature overrides",
    "payments:view": "View payments and revenue",
}
_ROLES = {   # name -> (description, is_super, permissions)
    "super_admin": ("Full access, including managing admins and roles", True, []),
    "admin": ("General administrator", False, [
        "users:view", "users:block", "users:delete", "users:manage_sessions", "messaging:send", "broadcast:send",
        "audit:view", "analytics:view", "content:manage", "plans:manage", "payments:view"]),
    "support_admin": ("Customer support", False, ["users:view", "users:block", "users:manage_sessions", "messaging:send"]),
    "content_admin": ("Content management", False, ["users:view", "content:manage"]),
    "finance_admin": ("Finance", False, ["users:view", "analytics:view", "plans:manage", "payments:view"]),
    "analytics_admin": ("Analytics", False, ["users:view", "analytics:view", "audit:view"]),
    "moderation_admin": ("Moderation", False, ["users:view", "users:block", "users:manage_sessions", "audit:view"]),
}
_LEGACY_ENUM_VALUES = ("super_admin", "admin", "support_admin", "content_admin", "finance_admin",
                       "analytics_admin", "moderation_admin")
# When restoring the single-role column, an admin holding several roles keeps the most powerful one.
_LEGACY_PRIORITY = _LEGACY_ENUM_VALUES


def _ts_columns():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    permissions = op.create_table(
        "admin_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        *_ts_columns(),
    )
    op.create_index("ix_admin_permissions_code", "admin_permissions", ["code"], unique=True)

    roles = op.create_table(
        "admin_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_super", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_ts_columns(),
    )
    op.create_index("ix_admin_roles_name", "admin_roles", ["name"], unique=True)

    role_permissions = op.create_table(
        "admin_role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["role_id"], ["admin_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["admin_permissions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_admin_role_permission"),
    )
    op.create_index("ix_admin_role_permissions_role_id", "admin_role_permissions", ["role_id"])
    op.create_index("ix_admin_role_permissions_permission_id", "admin_role_permissions", ["permission_id"])

    user_roles = op.create_table(
        "admin_user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["admin_roles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.UniqueConstraint("admin_user_id", "role_id", name="uq_admin_user_role"),
    )
    op.create_index("ix_admin_user_roles_admin_user_id", "admin_user_roles", ["admin_user_id"])
    op.create_index("ix_admin_user_roles_role_id", "admin_user_roles", ["role_id"])

    # --- seed the catalog and the system roles ---
    permission_ids = {code: uuid.uuid4() for code in _PERMISSIONS}
    op.bulk_insert(permissions, [{"id": permission_ids[c], "code": c, "description": d} for c, d in _PERMISSIONS.items()])
    role_ids = {name: uuid.uuid4() for name in _ROLES}
    op.bulk_insert(roles, [
        {"id": role_ids[n], "name": n, "description": desc, "is_system": True, "is_super": is_super}
        for n, (desc, is_super, _) in _ROLES.items()
    ])
    op.bulk_insert(role_permissions, [
        {"id": uuid.uuid4(), "role_id": role_ids[n], "permission_id": permission_ids[code]}
        for n, (_, _, codes) in _ROLES.items() for code in codes
    ])

    # --- carry over every existing admin's single role ---
    existing = bind.execute(sa.text("SELECT id, role::text AS role FROM admin_users")).fetchall()
    op.bulk_insert(user_roles, [
        {"id": uuid.uuid4(), "admin_user_id": row.id, "role_id": role_ids[row.role]} for row in existing
    ])

    op.drop_column("admin_users", "role")
    sa.Enum(name="admin_role_enum").drop(bind, checkfirst=True)

    # --- append-only audit log, enforced by the database ---
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_audit_log_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'admin_audit_logs is append-only (% is not allowed)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_admin_audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON admin_audit_logs
        FOR EACH ROW EXECUTE FUNCTION forbid_audit_log_change();
    """)
    op.execute("""
        CREATE TRIGGER trg_admin_audit_logs_no_truncate
        BEFORE TRUNCATE ON admin_audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION forbid_audit_log_change();
    """)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER IF EXISTS trg_admin_audit_logs_no_truncate ON admin_audit_logs")
    op.execute("DROP TRIGGER IF EXISTS trg_admin_audit_logs_no_update_delete ON admin_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS forbid_audit_log_change()")

    legacy = sa.Enum(*_LEGACY_ENUM_VALUES, name="admin_role_enum")
    legacy.create(bind, checkfirst=True)
    op.add_column("admin_users", sa.Column("role", legacy, nullable=True))
    rows = bind.execute(sa.text(
        "SELECT ur.admin_user_id AS admin_id, r.name AS name FROM admin_user_roles ur JOIN admin_roles r ON r.id = ur.role_id"
    )).fetchall()
    best: dict = {}
    for row in rows:
        rank = _LEGACY_PRIORITY.index(row.name) if row.name in _LEGACY_PRIORITY else len(_LEGACY_PRIORITY)
        if row.admin_id not in best or rank < best[row.admin_id][0]:
            best[row.admin_id] = (rank, row.name if row.name in _LEGACY_PRIORITY else "admin")
    for admin_id, (_, name) in best.items():
        bind.execute(sa.text("UPDATE admin_users SET role = CAST(:r AS admin_role_enum) WHERE id = :i"), {"r": name, "i": admin_id})
    bind.execute(sa.text("UPDATE admin_users SET role = CAST('admin' AS admin_role_enum) WHERE role IS NULL"))
    op.alter_column("admin_users", "role", nullable=False)

    op.drop_index("ix_admin_user_roles_role_id", table_name="admin_user_roles")
    op.drop_index("ix_admin_user_roles_admin_user_id", table_name="admin_user_roles")
    op.drop_table("admin_user_roles")
    op.drop_index("ix_admin_role_permissions_permission_id", table_name="admin_role_permissions")
    op.drop_index("ix_admin_role_permissions_role_id", table_name="admin_role_permissions")
    op.drop_table("admin_role_permissions")
    op.drop_index("ix_admin_roles_name", table_name="admin_roles")
    op.drop_table("admin_roles")
    op.drop_index("ix_admin_permissions_code", table_name="admin_permissions")
    op.drop_table("admin_permissions")
