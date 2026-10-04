"""
AdminService (Master Blueprint §54-56) — database-driven RBAC.

The single place that:
  - resolves whether a user is an ACTIVE admin and what they may do: the union
    of the permissions of every role they hold (a role flagged `is_super`
    implies everything) — read from the database on every check, so a change
    to a role or an admin takes effect immediately;
  - enforces permission checks, and AUDITS every denied attempt;
  - manages the roster and the roles themselves. That is a Super Admin power and
    is not delegable (§54: normal admins can never create Super Admins — here
    they cannot create or change any admin or role at all);
  - writes the audit log — every sensitive action calls `log()` so the trail's
    shape stays consistent.

Safety rails on the roster:
  * an admin always holds at least one role;
  * the LAST active Super Admin can never be disabled or demoted (nobody could
    manage admins again);
  * nobody can disable their own admin access;
  * system roles cannot be deleted or renamed; the super role cannot be edited;
  * a role that is still assigned cannot be deleted;
  * only permissions that the code actually checks (the catalog) can be granted.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    ALL_PERMISSION_CODES,
    AdminAccess,
    is_last_super_admin_change,
    resolve_access,
    unknown_permissions,
    validate_role_name,
)
from app.core.constants import AuditResult
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models.admin import AdminAuditLog, AdminRoleModel, AdminUser
from app.repositories.admin_repository import AdminRepository
from app.repositories.rbac_repository import RbacRepository
from app.repositories.user_repository import UserRepository


@dataclass
class AdminSummary:
    admin: AdminUser
    access: AdminAccess


@dataclass
class RoleSummary:
    role: AdminRoleModel
    permissions: frozenset[str]
    admin_count: int


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AdminRepository(db)
        self.rbac = RbacRepository(db)
        self.user_repo = UserRepository(db)

    # ------------------------------------------------------------------
    # Access resolution
    # ------------------------------------------------------------------
    async def get_active_admin(self, user_id: uuid.UUID) -> Optional[AdminUser]:
        admin = await self.repo.get_admin_by_user_id(user_id)
        if admin is not None and admin.is_active:
            return admin
        return None

    async def get_access(self, admin: AdminUser) -> AdminAccess:
        grants = await self.rbac.grants_for_admins([admin.id])
        return resolve_access(grants.get(admin.id, []))

    async def require_permission(self, *, user_id: uuid.UUID, permission: str) -> AdminUser:
        if permission not in ALL_PERMISSION_CODES:
            # A programming error (typo / permission missing from the catalog):
            # fail loudly instead of quietly locking everyone out or letting it through.
            raise RuntimeError(f"Unknown admin permission '{permission}'. Add it to PERMISSION_CATALOG.")
        admin = await self.get_active_admin(user_id)
        if admin is None:
            raise ForbiddenError("Admin access required.")
        access = await self.get_access(admin)
        if not access.allows(permission):
            await self._audit_denied(admin, permission)
            raise ForbiddenError(f"Your admin roles do not include the '{permission}' permission.")
        return admin

    async def require_super_admin(self, user_id: uuid.UUID) -> AdminUser:
        admin = await self.get_active_admin(user_id)
        if admin is None:
            raise ForbiddenError("This action requires super admin access.")
        if not (await self.get_access(admin)).is_super:
            await self._audit_denied(admin, "super_admin")
            raise ForbiddenError("This action requires super admin access.")
        return admin

    async def my_access(self, user_id: uuid.UUID) -> AdminSummary:
        admin = await self.get_active_admin(user_id)
        if admin is None:
            raise ForbiddenError("Admin access required.")
        return AdminSummary(admin=admin, access=await self.get_access(admin))

    async def _audit_denied(self, admin: AdminUser, permission: str) -> None:
        """Committed immediately: the request is about to fail, and the request-scoped
        transaction is rolled back on exceptions — the attempt must still be recorded."""
        await self.log(
            admin_user_id=admin.user_id, action="permission.denied", target_type="permission",
            target_id=permission, result=AuditResult.FAILURE, metadata={"permission": permission},
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # Roster (Super Admin only)
    # ------------------------------------------------------------------
    async def list_admins(self, actor_id: uuid.UUID) -> list[AdminSummary]:
        if await self.get_active_admin(actor_id) is None:
            raise ForbiddenError("Admin access required.")
        admins = list(await self.repo.list_admins())
        grants = await self.rbac.grants_for_admins(a.id for a in admins)
        return [AdminSummary(admin=a, access=resolve_access(grants.get(a.id, []))) for a in admins]

    async def create_admin(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, role_names: Iterable[str],
        ip_address: Optional[str] = None,
    ) -> AdminSummary:
        await self.require_super_admin(actor_id)

        target_user = await self.user_repo.get_by_id(target_user_id)
        if target_user is None:
            raise NotFoundError("User not found.")
        if not target_user.is_active:
            raise ValidationAppError("Only active, verified accounts can become admins.")
        if await self.repo.get_admin_by_user_id(target_user_id) is not None:
            raise ConflictError("This user is already an admin.")

        roles = await self._resolve_roles(role_names)
        admin = await self.repo.create_admin(AdminUser(user_id=target_user_id, is_active=True, created_by=actor_id))
        await self.rbac.set_admin_roles(admin.id, {r.id for r in roles}, actor_id)
        await self.log(
            admin_user_id=actor_id, action="admin.create", target_type="user", target_id=str(target_user_id),
            result=AuditResult.SUCCESS, metadata={"roles": sorted(r.name for r in roles)}, ip_address=ip_address,
        )
        await self.db.commit()
        return AdminSummary(admin=admin, access=await self.get_access(admin))

    async def set_admin_roles(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, role_names: Iterable[str],
        ip_address: Optional[str] = None,
    ) -> AdminSummary:
        await self.require_super_admin(actor_id)
        admin = await self._get_admin(target_user_id)
        roles = await self._resolve_roles(role_names)

        before = await self.get_access(admin)
        will_be_super = any(r.is_super for r in roles)
        if is_last_super_admin_change(
            was_super=before.is_super, will_be_super=will_be_super, will_be_active=admin.is_active,
            other_active_super_admins=await self.rbac.count_active_super_admins(excluding_admin_id=admin.id),
        ):
            raise ConflictError("This is the last active Super Admin; at least one must remain.")

        await self.rbac.set_admin_roles(admin.id, {r.id for r in roles}, actor_id)
        await self.log(
            admin_user_id=actor_id, action="admin.set_roles", target_type="user", target_id=str(target_user_id),
            result=AuditResult.SUCCESS, ip_address=ip_address,
            metadata={"before": list(before.role_names), "after": sorted(r.name for r in roles)},
        )
        await self.db.commit()
        return AdminSummary(admin=admin, access=await self.get_access(admin))

    async def disable_admin(
        self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID, ip_address: Optional[str] = None
    ) -> AdminUser:
        await self.require_super_admin(actor_id)
        if actor_id == target_user_id:
            raise ConflictError("You cannot disable your own admin access.")
        admin = await self._get_admin(target_user_id)

        access = await self.get_access(admin)
        if is_last_super_admin_change(
            was_super=access.is_super, will_be_super=access.is_super, will_be_active=False,
            other_active_super_admins=await self.rbac.count_active_super_admins(excluding_admin_id=admin.id),
        ):
            raise ConflictError("This is the last active Super Admin; at least one must remain.")

        admin.is_active = False
        await self.repo.save_admin(admin)
        await self.log(admin_user_id=actor_id, action="admin.disable", target_type="user",
                       target_id=str(target_user_id), result=AuditResult.SUCCESS, ip_address=ip_address)
        await self.db.commit()
        return admin

    async def _get_admin(self, user_id: uuid.UUID) -> AdminUser:
        admin = await self.repo.get_admin_by_user_id(user_id)
        if admin is None:
            raise NotFoundError("This user is not an admin.")
        return admin

    async def _resolve_roles(self, role_names: Iterable[str]) -> list[AdminRoleModel]:
        names = sorted({str(n).strip().lower() for n in role_names if str(n).strip()})
        if not names:
            raise ValidationAppError("An admin must hold at least one role.")
        roles = await self.rbac.get_roles_by_names(names)
        missing = sorted(set(names) - {r.name for r in roles})
        if missing:
            raise NotFoundError(f"Unknown role(s): {', '.join(missing)}.")
        return roles

    # ------------------------------------------------------------------
    # Roles and permissions
    # ------------------------------------------------------------------
    async def list_permissions(self, actor_id: uuid.UUID):
        if await self.get_active_admin(actor_id) is None:
            raise ForbiddenError("Admin access required.")
        return await self.rbac.list_permissions()

    async def list_roles(self, actor_id: uuid.UUID) -> list[RoleSummary]:
        if await self.get_active_admin(actor_id) is None:
            raise ForbiddenError("Admin access required.")
        roles = list(await self.rbac.list_roles())
        codes = await self.rbac.permission_codes_for_roles(r.id for r in roles)
        return [
            RoleSummary(role=r, permissions=frozenset(codes.get(r.id, set())),
                        admin_count=await self.rbac.count_admins_holding_role(r.id))
            for r in roles
        ]

    async def create_role(
        self, *, actor_id: uuid.UUID, name: str, description: str, permissions: Iterable[str],
        ip_address: Optional[str] = None,
    ) -> RoleSummary:
        await self.require_super_admin(actor_id)
        try:
            name = validate_role_name(name)
        except ValueError as exc:
            raise ValidationAppError(str(exc)) from exc
        codes = self._checked_permissions(permissions)
        if await self.rbac.get_roles_by_names([name]):
            raise ConflictError(f"A role named '{name}' already exists.")

        role = await self.rbac.create_role(AdminRoleModel(name=name, description=description, is_system=False, is_super=False))
        await self.rbac.set_role_permissions(role.id, set((await self.rbac.permission_ids_by_code(codes)).values()))
        await self.log(admin_user_id=actor_id, action="role.create", target_type="role", target_id=name,
                       result=AuditResult.SUCCESS, metadata={"permissions": sorted(codes)}, ip_address=ip_address)
        await self.db.commit()
        return RoleSummary(role=role, permissions=frozenset(codes), admin_count=0)

    async def update_role(
        self, *, actor_id: uuid.UUID, role_id: uuid.UUID, description: Optional[str] = None,
        permissions: Optional[Iterable[str]] = None, ip_address: Optional[str] = None,
    ) -> RoleSummary:
        await self.require_super_admin(actor_id)
        role = await self.rbac.get_role(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        if role.is_super:
            raise ValidationAppError("The super admin role implies every permission and cannot be edited.")

        before = (await self.rbac.permission_codes_for_roles([role.id])).get(role.id, set())
        after = set(before)
        if permissions is not None:
            after = self._checked_permissions(permissions)
            await self.rbac.set_role_permissions(role.id, set((await self.rbac.permission_ids_by_code(after)).values()))
        if description is not None:
            role.description = description
            await self.rbac.save_role(role)
        await self.log(
            admin_user_id=actor_id, action="role.update", target_type="role", target_id=role.name,
            result=AuditResult.SUCCESS, ip_address=ip_address,
            metadata={"added": sorted(after - before), "removed": sorted(before - after)},
        )
        await self.db.commit()
        return RoleSummary(role=role, permissions=frozenset(after), admin_count=await self.rbac.count_admins_holding_role(role.id))

    async def delete_role(self, *, actor_id: uuid.UUID, role_id: uuid.UUID, ip_address: Optional[str] = None) -> None:
        await self.require_super_admin(actor_id)
        role = await self.rbac.get_role(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        if role.is_system:
            raise ValidationAppError("System roles cannot be deleted.")
        holders = await self.rbac.count_admins_holding_role(role.id)
        if holders:
            raise ConflictError(f"{holders} admin(s) still hold this role. Reassign them first.")
        name = role.name
        await self.rbac.delete_role(role)
        await self.log(admin_user_id=actor_id, action="role.delete", target_type="role", target_id=name,
                       result=AuditResult.SUCCESS, ip_address=ip_address)
        await self.db.commit()

    @staticmethod
    def _checked_permissions(permissions: Iterable[str]) -> set[str]:
        codes = {str(p).strip() for p in permissions if str(p).strip()}
        unknown = unknown_permissions(codes)
        if unknown:
            raise ValidationAppError(
                f"Unknown permission(s): {', '.join(unknown)}.", details={"valid_permissions": sorted(ALL_PERMISSION_CODES)}
            )
        return codes

    # ------------------------------------------------------------------
    # Audit logging (Blueprint §56)
    # ------------------------------------------------------------------
    async def log(
        self,
        *,
        admin_user_id: uuid.UUID,
        action: str,
        target_type: str,
        result: AuditResult,
        target_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> AdminAuditLog:
        entry = AdminAuditLog(
            admin_user_id=admin_user_id, action=action, target_type=target_type, target_id=target_id,
            result=result, reason=reason, extra_metadata=metadata, ip_address=ip_address,
        )
        return await self.repo.create_audit_log(entry)

    async def list_audit_logs(self, *, actor_id: uuid.UUID, limit: int = 50, offset: int = 0):
        await self.require_permission(user_id=actor_id, permission="audit:view")
        return await self.repo.list_audit_logs(limit=limit, offset=offset)
