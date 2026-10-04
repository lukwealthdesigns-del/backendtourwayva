"""System role defaults (the seed for a fresh database) behave as the Blueprint §55 roles intend."""
from app.core.admin_permissions import SYSTEM_ROLES, RoleGrant, resolve_access


def access_for(role_name):
    role = next(r for r in SYSTEM_ROLES if r.name == role_name)
    return resolve_access([RoleGrant(role.name, role.is_super, role.permissions)])


def test_super_admin_has_every_permission_even_ones_that_do_not_exist_yet():
    boss = access_for("super_admin")
    for permission in ("users:view", "users:block", "users:delete", "broadcast:send", "some:future-permission"):
        assert boss.allows(permission)


def test_content_admin_has_only_users_view_and_content():
    content = access_for("content_admin")
    assert content.allows("users:view") and content.allows("content:manage")
    assert not content.allows("users:block") and not content.allows("broadcast:send")


def test_support_admin_cannot_delete_users():
    support = access_for("support_admin")
    assert support.allows("users:block") and not support.allows("users:delete")


def test_finance_admin_cannot_block_users_but_sees_payments():
    finance = access_for("finance_admin")
    assert finance.allows("analytics:view") and finance.allows("payments:view") and not finance.allows("users:block")


def test_moderation_admin_can_view_the_audit_log_but_not_broadcast():
    moderation = access_for("moderation_admin")
    assert moderation.allows("audit:view") and not moderation.allows("broadcast:send")


def test_unknown_permission_strings_are_denied_for_non_super_roles():
    assert not access_for("admin").allows("totally:made-up")


def test_refunds_are_limited_to_finance_and_super_admin():
    # Refunding moves money OUT, so it is a separate permission from viewing payments,
    # and the general admin role deliberately does not get it by default.
    assert access_for("finance_admin").allows("payments:refund")
    assert access_for("super_admin").allows("payments:refund")
    for role in ("admin", "support_admin", "content_admin", "analytics_admin", "moderation_admin"):
        assert not access_for(role).allows("payments:refund"), role
