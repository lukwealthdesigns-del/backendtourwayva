"""
Database-driven admin RBAC: permission checks, denial auditing, roster and role
management, and the safety rails — with an in-memory fake of the repositories.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.admin_permissions import ALL_PERMISSION_CODES, SYSTEM_ROLES, RoleGrant
from app.core.constants import AuditResult
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models.admin import AdminRoleModel, AdminUser
from app.modules.admin.admin_service import AdminService


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _Store:
    """In-memory admin_users / roles / permissions / links + the audit log."""

    def __init__(self):
        self.admins: list[AdminUser] = []
        self.roles: dict[uuid.UUID, AdminRoleModel] = {}
        self.permission_ids = {code: uuid.uuid4() for code in ALL_PERMISSION_CODES}
        self.role_permissions: dict[uuid.UUID, set[str]] = {}
        self.admin_roles: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.audit: list = []
        self.users = {}
        for definition in SYSTEM_ROLES:
            role = AdminRoleModel(id=uuid.uuid4(), name=definition.name, description=definition.description,
                                  is_system=True, is_super=definition.is_super)
            self.roles[role.id] = role
            self.role_permissions[role.id] = set(definition.permissions)

    # helpers for tests
    def role(self, name):
        return next(r for r in self.roles.values() if r.name == name)

    def add_admin(self, *roles, active=True):
        user_id = uuid.uuid4()
        self.users[user_id] = NS(id=user_id, is_active=True)
        admin = AdminUser(id=uuid.uuid4(), user_id=user_id, is_active=active)
        self.admins.append(admin)
        self.admin_roles[admin.id] = {self.role(n).id for n in roles}
        return admin

    # ---- AdminRepository ----
    async def get_admin_by_user_id(self, user_id):
        return next((a for a in self.admins if a.user_id == user_id), None)

    async def create_admin(self, admin):
        admin.id = admin.id or uuid.uuid4()
        self.admins.append(admin)
        return admin

    async def save_admin(self, admin):
        return admin

    async def list_admins(self):
        return list(self.admins)

    async def create_audit_log(self, entry):
        self.audit.append(entry)
        return entry

    # ---- RbacRepository ----
    async def grants_for_admins(self, admin_ids):
        out = {}
        for admin_id in admin_ids:
            out[admin_id] = [
                RoleGrant(self.roles[r].name, self.roles[r].is_super, frozenset(self.role_permissions.get(r, set())))
                for r in self.admin_roles.get(admin_id, set())
            ]
        return out

    async def get_roles_by_names(self, names):
        wanted = set(names)
        return [r for r in self.roles.values() if r.name in wanted]

    async def get_role(self, role_id):
        return self.roles.get(role_id)

    async def list_roles(self):
        return sorted(self.roles.values(), key=lambda r: r.name)

    async def list_permissions(self):
        return [NS(code=c, description="") for c in sorted(self.permission_ids)]

    async def permission_codes_for_roles(self, role_ids):
        return {r: set(self.role_permissions.get(r, set())) for r in role_ids}

    async def permission_ids_by_code(self, codes):
        return {c: self.permission_ids[c] for c in set(codes) if c in self.permission_ids}

    async def set_admin_roles(self, admin_id, role_ids, assigned_by):
        self.admin_roles[admin_id] = set(role_ids)

    async def count_active_super_admins(self, *, excluding_admin_id=None):
        return sum(
            1 for a in self.admins
            if a.is_active and a.id != excluding_admin_id
            and any(self.roles[r].is_super for r in self.admin_roles.get(a.id, set()))
        )

    async def count_admins_holding_role(self, role_id):
        return sum(1 for roles in self.admin_roles.values() if role_id in roles)

    async def create_role(self, role):
        role.id = role.id or uuid.uuid4()
        self.roles[role.id] = role
        self.role_permissions[role.id] = set()
        return role

    async def save_role(self, role):
        return role

    async def delete_role(self, role):
        self.roles.pop(role.id, None)

    async def set_role_permissions(self, role_id, permission_ids):
        by_id = {v: k for k, v in self.permission_ids.items()}
        self.role_permissions[role_id] = {by_id[p] for p in permission_ids}

    # ---- UserRepository ----
    async def get_by_id(self, user_id):
        return self.users.get(user_id)


def _service():
    store = _Store()
    service = AdminService.__new__(AdminService)
    service.db, service.repo, service.rbac, service.user_repo = _FakeDB(), store, store, store
    return service, store


def _run(coro):
    return asyncio.run(coro)


def _new_user(store):
    user = NS(id=uuid.uuid4(), is_active=True)
    store.users[user.id] = user
    return user


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------
def test_permissions_are_the_union_of_every_role_the_admin_holds():
    service, store = _service()
    admin = store.add_admin("support_admin", "finance_admin")
    assert _run(service.require_permission(user_id=admin.user_id, permission="users:block")) is admin
    assert _run(service.require_permission(user_id=admin.user_id, permission="payments:view")) is admin
    with pytest.raises(ForbiddenError):
        _run(service.require_permission(user_id=admin.user_id, permission="users:delete"))


def test_a_denied_attempt_is_audited_and_committed_before_the_error():
    service, store = _service()
    admin = store.add_admin("content_admin")
    with pytest.raises(ForbiddenError, match="broadcast:send"):
        _run(service.require_permission(user_id=admin.user_id, permission="broadcast:send"))
    (entry,) = store.audit
    assert entry.action == "permission.denied" and entry.result == AuditResult.FAILURE
    assert entry.admin_user_id == admin.user_id and entry.target_id == "broadcast:send"
    assert service.db.commits == 1                     # survives the request rollback that follows the exception


def test_non_admins_and_disabled_admins_are_refused():
    service, store = _service()
    with pytest.raises(ForbiddenError):
        _run(service.require_permission(user_id=uuid.uuid4(), permission="users:view"))
    disabled = store.add_admin("admin", active=False)
    with pytest.raises(ForbiddenError):
        _run(service.require_permission(user_id=disabled.user_id, permission="users:view"))


def test_a_permission_missing_from_the_catalog_is_a_loud_programming_error():
    service, store = _service()
    boss = store.add_admin("super_admin")
    with pytest.raises(RuntimeError, match="Unknown admin permission"):
        _run(service.require_permission(user_id=boss.user_id, permission="typo:permission"))


def test_super_admin_passes_every_check_and_role_changes_apply_on_the_next_request():
    service, store = _service()
    boss, support = store.add_admin("super_admin"), store.add_admin("support_admin")
    assert _run(service.require_permission(user_id=boss.user_id, permission="payments:view")) is boss
    assert _run(service.require_permission(user_id=support.user_id, permission="users:block")) is support

    _run(service.update_role(actor_id=boss.user_id, role_id=store.role("support_admin").id, permissions=["users:view"]))
    with pytest.raises(ForbiddenError):                # no caching: revoked immediately
        _run(service.require_permission(user_id=support.user_id, permission="users:block"))


def test_only_super_admins_pass_the_super_check():
    service, store = _service()
    boss, normal = store.add_admin("super_admin"), store.add_admin("admin")
    assert _run(service.require_super_admin(boss.user_id)) is boss
    with pytest.raises(ForbiddenError):
        _run(service.require_super_admin(normal.user_id))
    assert store.audit[-1].action == "permission.denied"


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------
def test_only_a_super_admin_can_create_admins_and_an_admin_can_never_grant_the_super_role():
    service, store = _service()
    boss, normal, target = store.add_admin("super_admin"), store.add_admin("admin"), _new_user(store)
    with pytest.raises(ForbiddenError):
        _run(service.create_admin(actor_id=normal.user_id, target_user_id=target.id, role_names=["super_admin"]))
    assert not any(a.user_id == target.id for a in store.admins)

    summary = _run(service.create_admin(actor_id=boss.user_id, target_user_id=target.id, role_names=["support_admin", "Content_Admin "]))
    assert summary.access.role_names == ("content_admin", "support_admin") and not summary.access.is_super
    assert store.audit[-1].action == "admin.create" and store.audit[-1].extra_metadata["roles"] == ["content_admin", "support_admin"]


def test_create_admin_validation():
    service, store = _service()
    boss, target = store.add_admin("super_admin"), _new_user(store)
    with pytest.raises(ValidationAppError):
        _run(service.create_admin(actor_id=boss.user_id, target_user_id=target.id, role_names=[]))
    with pytest.raises(NotFoundError, match="ghost_role"):
        _run(service.create_admin(actor_id=boss.user_id, target_user_id=target.id, role_names=["ghost_role"]))
    with pytest.raises(NotFoundError):
        _run(service.create_admin(actor_id=boss.user_id, target_user_id=uuid.uuid4(), role_names=["admin"]))
    inactive = _new_user(store)
    inactive.is_active = False
    with pytest.raises(ValidationAppError):
        _run(service.create_admin(actor_id=boss.user_id, target_user_id=inactive.id, role_names=["admin"]))
    _run(service.create_admin(actor_id=boss.user_id, target_user_id=target.id, role_names=["admin"]))
    with pytest.raises(ConflictError):
        _run(service.create_admin(actor_id=boss.user_id, target_user_id=target.id, role_names=["admin"]))


def test_the_last_super_admin_cannot_be_demoted_or_disabled_but_can_once_another_exists():
    service, store = _service()
    only = store.add_admin("super_admin")
    with pytest.raises(ConflictError, match="last active Super Admin"):
        _run(service.set_admin_roles(actor_id=only.user_id, target_user_id=only.user_id, role_names=["admin"]))
    assert store.admin_roles[only.id] == {store.role("super_admin").id}

    second = store.add_admin("super_admin")
    _run(service.set_admin_roles(actor_id=second.user_id, target_user_id=only.user_id, role_names=["admin"]))
    assert store.admin_roles[only.id] == {store.role("admin").id}
    assert store.audit[-1].extra_metadata == {"before": ["super_admin"], "after": ["admin"]}

    with pytest.raises(ConflictError, match="last active Super Admin"):
        _run(service.set_admin_roles(actor_id=second.user_id, target_user_id=second.user_id, role_names=["support_admin"]))


def test_disabling_admins_has_guards_and_is_audited():
    service, store = _service()
    boss, other_boss, support = store.add_admin("super_admin"), store.add_admin("super_admin"), store.add_admin("support_admin")
    with pytest.raises(ConflictError, match="own admin access"):
        _run(service.disable_admin(actor_id=boss.user_id, target_user_id=boss.user_id))
    _run(service.disable_admin(actor_id=boss.user_id, target_user_id=support.user_id))
    assert support.is_active is False and store.audit[-1].action == "admin.disable"
    with pytest.raises(ForbiddenError):                # a disabled admin has no access at all
        _run(service.require_permission(user_id=support.user_id, permission="users:view"))
    _run(service.disable_admin(actor_id=boss.user_id, target_user_id=other_boss.user_id))      # boss remains
    solo = _service()
    solo_service, solo_store = solo
    only = solo_store.add_admin("super_admin")
    lone_admin = solo_store.add_admin("admin")
    with pytest.raises(NotFoundError):
        _run(solo_service.disable_admin(actor_id=only.user_id, target_user_id=uuid.uuid4()))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def test_super_admin_can_create_a_custom_role_from_catalog_permissions():
    service, store = _service()
    boss = store.add_admin("super_admin")
    summary = _run(service.create_role(actor_id=boss.user_id, name="  Trip_Auditor ", description="reads trips",
                                       permissions=["users:view", "audit:view"]))
    assert summary.role.name == "trip_auditor" and not summary.role.is_system and not summary.role.is_super
    assert summary.permissions == {"users:view", "audit:view"}
    assert store.role_permissions[summary.role.id] == {"users:view", "audit:view"}
    assert store.audit[-1].action == "role.create"

    holder = store.add_admin()
    _run(service.set_admin_roles(actor_id=boss.user_id, target_user_id=holder.user_id, role_names=["trip_auditor"]))
    assert _run(service.require_permission(user_id=holder.user_id, permission="audit:view")) is holder


def test_role_creation_rules():
    service, store = _service()
    boss, normal = store.add_admin("super_admin"), store.add_admin("admin")
    with pytest.raises(ForbiddenError):
        _run(service.create_role(actor_id=normal.user_id, name="mine", description="", permissions=[]))
    with pytest.raises(ValidationAppError):
        _run(service.create_role(actor_id=boss.user_id, name="Bad Name!", description="", permissions=[]))
    with pytest.raises(ConflictError):
        _run(service.create_role(actor_id=boss.user_id, name="support_admin", description="", permissions=[]))
    with pytest.raises(ValidationAppError, match="made:up"):           # only permissions the code checks
        _run(service.create_role(actor_id=boss.user_id, name="ok_role", description="", permissions=["made:up"]))


def test_updating_a_role_replaces_permissions_and_audits_the_diff_but_never_the_super_role():
    service, store = _service()
    boss = store.add_admin("super_admin")
    role = store.role("finance_admin")
    summary = _run(service.update_role(actor_id=boss.user_id, role_id=role.id, permissions=["users:view", "audit:view"],
                                       description="Finance (limited)"))
    assert summary.permissions == {"users:view", "audit:view"} and role.description == "Finance (limited)"
    meta = store.audit[-1].extra_metadata
    assert meta["added"] == ["audit:view"] and set(meta["removed"]) == {"analytics:view", "payments:view", "plans:manage"}
    with pytest.raises(ValidationAppError, match="cannot be edited"):
        _run(service.update_role(actor_id=boss.user_id, role_id=store.role("super_admin").id, permissions=[]))
    with pytest.raises(NotFoundError):
        _run(service.update_role(actor_id=boss.user_id, role_id=uuid.uuid4(), description="x"))


def test_role_deletion_rules():
    service, store = _service()
    boss = store.add_admin("super_admin")
    custom = _run(service.create_role(actor_id=boss.user_id, name="temp_role", description="", permissions=[])).role
    holder = store.add_admin()
    store.admin_roles[holder.id] = {custom.id}

    with pytest.raises(ValidationAppError, match="System roles"):
        _run(service.delete_role(actor_id=boss.user_id, role_id=store.role("admin").id))
    with pytest.raises(ConflictError, match="1 admin"):
        _run(service.delete_role(actor_id=boss.user_id, role_id=custom.id))
    store.admin_roles[holder.id] = {store.role("admin").id}
    _run(service.delete_role(actor_id=boss.user_id, role_id=custom.id))
    assert custom.id not in store.roles and store.audit[-1].action == "role.delete"


def test_listing_requires_an_admin_and_reports_holders():
    service, store = _service()
    boss = store.add_admin("super_admin")
    store.add_admin("support_admin")
    with pytest.raises(ForbiddenError):
        _run(service.list_roles(uuid.uuid4()))
    roles = {r.role.name: r for r in _run(service.list_roles(boss.user_id))}
    assert roles["support_admin"].admin_count == 1 and roles["support_admin"].permissions >= {"users:view"}
    admins = _run(service.list_admins(boss.user_id))
    assert {a.admin.id for a in admins} == {a.id for a in store.admins}
    assert next(a for a in admins if a.admin.id == boss.id).access.is_super
    assert len(_run(service.list_permissions(boss.user_id))) == len(ALL_PERMISSION_CODES)
