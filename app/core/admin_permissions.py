"""
Admin RBAC building blocks (Master Blueprint §54-55, Prompt §5).

Roles and their permissions now live in the DATABASE (admin_roles,
admin_permissions, admin_role_permissions, admin_user_roles) so a Super Admin
can create roles and change what they may do at runtime, and an admin can hold
several roles. This module holds only what must stay in code:

  * PERMISSION_CATALOG — the permissions the code actually CHECKS. A permission
    is a string an endpoint tests for (`users:block`, `payments:view`, ...);
    it cannot be invented at runtime because nothing would enforce it. Roles are
    composed FROM this catalog. Adding a permission = add it here (and use it in
    an endpoint); `sync_rbac_catalog` inserts it and grants it to the system
    roles that list it in their defaults.
  * SYSTEM_ROLES — the roles of Blueprint §55 with their DEFAULT permissions,
    used to seed a fresh database. After seeding, the database is the source
    of truth: a Super Admin may edit a system role's permissions (SUPER_ADMIN
    excepted) and the edit is never overwritten by a later sync.
  * pure resolution rules — union over an admin's roles; a role flagged
    `is_super` implies every permission (so a new permission never has to be
    remembered for the Super Admin).

Managing admins and roles is deliberately NOT a delegable permission: only a
Super Admin can do it (Blueprint §54: "Never allow normal admins to create Super
Admins unless explicitly authorized"; here nothing but a Super Admin can create
or change any admin at all).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.core.constants import AdminRole


@dataclass(frozen=True)
class PermissionDef:
    code: str
    description: str


PERMISSION_CATALOG: tuple[PermissionDef, ...] = (
    PermissionDef("users:view", "View and search user accounts"),
    PermissionDef("users:block", "Block, unblock and suspend users"),
    PermissionDef("users:delete", "Delete user accounts"),
    PermissionDef("users:manage_sessions", "Revoke a user's sessions"),
    PermissionDef("messaging:send", "Send direct messages to users"),
    PermissionDef("broadcast:send", "Send broadcast messages to user segments"),
    PermissionDef("audit:view", "View the admin audit log"),
    PermissionDef("analytics:view", "View analytics and AI cost dashboards"),
    PermissionDef("content:manage", "Manage shared travel content (places, knowledge base)"),
    PermissionDef("plans:manage", "Manage plans, trials and per-user feature overrides"),
    PermissionDef("payments:view", "View payments and revenue"),
    PermissionDef("payments:refund", "Issue refunds for verified payments"),
    PermissionDef("flags:manage", "Switch features on/off globally (kill switch, open to all)"),
)
ALL_PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSION_CATALOG)


@dataclass(frozen=True)
class SystemRoleDef:
    name: str
    description: str
    permissions: frozenset[str] = frozenset()
    is_super: bool = False


SYSTEM_ROLES: tuple[SystemRoleDef, ...] = (
    SystemRoleDef(AdminRole.SUPER_ADMIN.value, "Full access, including managing admins and roles", is_super=True),
    SystemRoleDef(AdminRole.ADMIN.value, "General administrator", frozenset({
        "users:view", "users:block", "users:delete", "users:manage_sessions", "messaging:send", "broadcast:send",
        "audit:view", "analytics:view", "content:manage", "plans:manage", "payments:view", "flags:manage",
    })),
    SystemRoleDef(AdminRole.SUPPORT_ADMIN.value, "Customer support",
                  frozenset({"users:view", "users:block", "users:manage_sessions", "messaging:send"})),
    SystemRoleDef(AdminRole.CONTENT_ADMIN.value, "Content management", frozenset({"users:view", "content:manage"})),
    SystemRoleDef(AdminRole.FINANCE_ADMIN.value, "Finance",
                  frozenset({"users:view", "analytics:view", "plans:manage", "payments:view", "payments:refund"})),
    SystemRoleDef(AdminRole.ANALYTICS_ADMIN.value, "Analytics", frozenset({"users:view", "analytics:view", "audit:view"})),
    SystemRoleDef(AdminRole.MODERATION_ADMIN.value, "Moderation",
                  frozenset({"users:view", "users:block", "users:manage_sessions", "audit:view"})),
)
SYSTEM_ROLE_NAMES: frozenset[str] = frozenset(r.name for r in SYSTEM_ROLES)
SUPER_ADMIN_ROLE_NAME = AdminRole.SUPER_ADMIN.value


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AdminAccess:
    """What an admin may do: the union of their roles' permissions."""

    role_names: tuple[str, ...] = ()
    is_super: bool = False
    permissions: frozenset[str] = frozenset()

    def allows(self, permission: str) -> bool:
        return self.is_super or permission in self.permissions

    def effective_permissions(self) -> frozenset[str]:
        """What to show in an admin UI: everything for a Super Admin."""
        return ALL_PERMISSION_CODES if self.is_super else self.permissions & ALL_PERMISSION_CODES


@dataclass(frozen=True)
class RoleGrant:
    name: str
    is_super: bool
    permissions: frozenset[str] = frozenset()


def resolve_access(roles: Iterable[RoleGrant]) -> AdminAccess:
    granted = list(roles)
    permissions: set[str] = set()
    for role in granted:
        permissions |= role.permissions
    return AdminAccess(
        role_names=tuple(sorted(r.name for r in granted)),
        is_super=any(r.is_super for r in granted),
        permissions=frozenset(permissions),
    )


_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


def validate_role_name(name: str) -> str:
    """Lower snake_case, 3-50 chars, starts with a letter."""
    cleaned = (name or "").strip().lower()
    if not _ROLE_NAME.match(cleaned):
        raise ValueError("Role names must be 3-50 characters: lowercase letters, digits and underscores, starting with a letter.")
    return cleaned


def unknown_permissions(codes: Iterable[str]) -> list[str]:
    return sorted({c for c in codes if c not in ALL_PERMISSION_CODES})


def is_last_super_admin_change(
    *, was_super: bool, will_be_super: bool, will_be_active: bool, other_active_super_admins: int
) -> bool:
    """True when an admin change would leave the platform with NO active Super
    Admin — nobody could then manage admins or roles again."""
    loses_super_power = was_super and (not will_be_super or not will_be_active)
    return loses_super_power and other_active_super_admins == 0


def new_permissions_for_system_roles(new_codes: Iterable[str]) -> dict[str, frozenset[str]]:
    """For a permission that was JUST added to the database, which system roles
    list it in their defaults (and so should be granted it)."""
    fresh = frozenset(new_codes)
    grants: dict[str, frozenset[str]] = {}
    for role in SYSTEM_ROLES:
        if not role.is_super:
            wanted = role.permissions & fresh
            if wanted:
                grants[role.name] = wanted
    return grants
