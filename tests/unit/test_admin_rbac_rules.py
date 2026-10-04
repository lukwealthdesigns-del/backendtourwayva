"""Pure RBAC rules: the permission catalog, system roles, access resolution, guards."""
import re
from pathlib import Path

import pytest

from app.core.admin_permissions import (
    ALL_PERMISSION_CODES,
    PERMISSION_CATALOG,
    SYSTEM_ROLE_NAMES,
    SYSTEM_ROLES,
    AdminAccess,
    RoleGrant,
    is_last_super_admin_change,
    new_permissions_for_system_roles,
    resolve_access,
    unknown_permissions,
    validate_role_name,
)
from app.core.constants import AdminRole


def test_catalog_is_unique_and_system_roles_only_use_catalog_permissions():
    codes = [p.code for p in PERMISSION_CATALOG]
    assert len(codes) == len(set(codes)) and all(re.fullmatch(r"[a-z_]+:[a-z_]+", c) for c in codes)
    for role in SYSTEM_ROLES:
        assert role.permissions <= ALL_PERMISSION_CODES, role.name


def test_system_roles_match_the_blueprint_role_list_and_only_super_admin_is_super():
    assert SYSTEM_ROLE_NAMES == {r.value for r in AdminRole}
    assert [r.name for r in SYSTEM_ROLES if r.is_super] == ["super_admin"]


def test_every_permission_the_code_checks_is_in_the_catalog():
    """A typo'd permission string in an endpoint would silently lock everyone but
    Super Admins out (or, worse, look like it works). Scan the source."""
    root = Path(__file__).resolve().parents[2] / "app"
    used: set[str] = set()
    pattern = re.compile(r"""(?:require_admin_permission\(|permission\s*=\s*)["']([a-z_]+:[a-z_]+)["']""")
    for path in root.rglob("*.py"):
        used |= set(pattern.findall(path.read_text()))
    assert used, "expected to find permission checks"
    assert used <= ALL_PERMISSION_CODES, sorted(used - ALL_PERMISSION_CODES)


def test_resolution_is_the_union_of_roles_and_super_implies_everything():
    support = RoleGrant("support_admin", False, frozenset({"users:view", "messaging:send"}))
    finance = RoleGrant("finance_admin", False, frozenset({"payments:view"}))
    both = resolve_access([support, finance])
    assert both.role_names == ("finance_admin", "support_admin") and not both.is_super
    assert both.allows("users:view") and both.allows("payments:view") and not both.allows("users:delete")

    boss = resolve_access([RoleGrant("super_admin", True)])
    assert boss.is_super and boss.allows("anything:at_all")
    assert boss.effective_permissions() == ALL_PERMISSION_CODES
    assert resolve_access([]).allows("users:view") is False


def test_a_permission_stored_in_the_database_but_not_in_the_catalog_is_ignored_for_display():
    access = AdminAccess(role_names=("x",), permissions=frozenset({"users:view", "ghost:permission"}))
    assert access.effective_permissions() == frozenset({"users:view"})


@pytest.mark.parametrize("name,ok", [("moderator_eu", True), ("  Trip_Auditor ", True), ("ab", False),
                                     ("1abc", False), ("has space", False), ("Has-Dash", False), ("x" * 51, False)])
def test_role_names(name, ok):
    if ok:
        assert validate_role_name(name) == name.strip().lower()
    else:
        with pytest.raises(ValueError):
            validate_role_name(name)


def test_unknown_permissions_are_reported():
    assert unknown_permissions(["users:view", "nope:x", "nope:x", "also:bad"]) == ["also:bad", "nope:x"]
    assert unknown_permissions(["users:view"]) == []


def test_the_last_super_admin_can_never_be_demoted_disabled_or_removed():
    kwargs = dict(was_super=True, will_be_super=True, will_be_active=True)
    assert not is_last_super_admin_change(**kwargs, other_active_super_admins=0)                    # no change of power
    assert is_last_super_admin_change(was_super=True, will_be_super=False, will_be_active=True, other_active_super_admins=0)
    assert is_last_super_admin_change(was_super=True, will_be_super=True, will_be_active=False, other_active_super_admins=0)
    assert not is_last_super_admin_change(was_super=True, will_be_super=False, will_be_active=True, other_active_super_admins=1)
    assert not is_last_super_admin_change(was_super=False, will_be_super=False, will_be_active=False, other_active_super_admins=0)


def test_a_new_permission_is_granted_only_to_system_roles_that_default_to_it():
    grants = new_permissions_for_system_roles(["payments:view"])
    assert set(grants) == {"admin", "finance_admin"} and "super_admin" not in grants   # super needs no explicit grant
    assert new_permissions_for_system_roles([]) == {}
