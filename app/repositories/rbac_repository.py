"""RBAC repository: permissions, roles, role-permission and admin-role links."""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import RoleGrant
from app.db.models.admin import (
    AdminPermission,
    AdminRoleModel,
    AdminRolePermission,
    AdminUser,
    AdminUserRole,
)


class RbacRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Permissions ---
    async def list_permissions(self) -> Sequence[AdminPermission]:
        result = await self.db.execute(select(AdminPermission).order_by(AdminPermission.code))
        return result.scalars().all()

    async def permission_ids_by_code(self, codes: Iterable[str]) -> dict[str, uuid.UUID]:
        wanted = list(set(codes))
        if not wanted:
            return {}
        result = await self.db.execute(select(AdminPermission.code, AdminPermission.id).where(AdminPermission.code.in_(wanted)))
        return {code: pid for code, pid in result.all()}

    # --- Roles ---
    async def get_role(self, role_id: uuid.UUID) -> Optional[AdminRoleModel]:
        result = await self.db.execute(select(AdminRoleModel).where(AdminRoleModel.id == role_id))
        return result.scalar_one_or_none()

    async def get_roles_by_names(self, names: Iterable[str]) -> list[AdminRoleModel]:
        wanted = list(set(names))
        if not wanted:
            return []
        result = await self.db.execute(select(AdminRoleModel).where(AdminRoleModel.name.in_(wanted)))
        return list(result.scalars().all())

    async def list_roles(self) -> Sequence[AdminRoleModel]:
        result = await self.db.execute(select(AdminRoleModel).order_by(AdminRoleModel.name))
        return result.scalars().all()

    async def create_role(self, role: AdminRoleModel) -> AdminRoleModel:
        self.db.add(role)
        await self.db.flush()
        return role

    async def save_role(self, role: AdminRoleModel) -> AdminRoleModel:
        await self.db.flush()
        return role

    async def delete_role(self, role: AdminRoleModel) -> None:
        await self.db.delete(role)
        await self.db.flush()

    async def permission_codes_for_roles(self, role_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
        ids = list(set(role_ids))
        codes: dict[uuid.UUID, set[str]] = defaultdict(set)
        if not ids:
            return codes
        result = await self.db.execute(
            select(AdminRolePermission.role_id, AdminPermission.code)
            .join(AdminPermission, AdminPermission.id == AdminRolePermission.permission_id)
            .where(AdminRolePermission.role_id.in_(ids))
        )
        for role_id, code in result.all():
            codes[role_id].add(code)
        return codes

    async def set_role_permissions(self, role_id: uuid.UUID, permission_ids: set[uuid.UUID]) -> None:
        """Make the role hold exactly `permission_ids`."""
        result = await self.db.execute(
            select(AdminRolePermission.permission_id).where(AdminRolePermission.role_id == role_id)
        )
        current = {row[0] for row in result.all()}
        removed = current - permission_ids
        if removed:
            await self.db.execute(delete(AdminRolePermission).where(
                AdminRolePermission.role_id == role_id, AdminRolePermission.permission_id.in_(removed)))
        for permission_id in permission_ids - current:
            self.db.add(AdminRolePermission(role_id=role_id, permission_id=permission_id))
        await self.db.flush()

    async def add_role_permissions(self, role_id: uuid.UUID, permission_ids: Iterable[uuid.UUID]) -> None:
        result = await self.db.execute(
            select(AdminRolePermission.permission_id).where(AdminRolePermission.role_id == role_id)
        )
        current = {row[0] for row in result.all()}
        for permission_id in set(permission_ids) - current:
            self.db.add(AdminRolePermission(role_id=role_id, permission_id=permission_id))
        await self.db.flush()

    async def count_admins_holding_role(self, role_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(AdminUserRole).where(AdminUserRole.role_id == role_id)
        )
        return int(result.scalar_one())

    # --- Admin <-> roles ---
    async def grants_for_admins(self, admin_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[RoleGrant]]:
        ids = list(set(admin_ids))
        if not ids:
            return {}
        result = await self.db.execute(
            select(AdminUserRole.admin_user_id, AdminRoleModel.id, AdminRoleModel.name, AdminRoleModel.is_super,
                   AdminPermission.code)
            .select_from(AdminUserRole)
            .join(AdminRoleModel, AdminRoleModel.id == AdminUserRole.role_id)
            .outerjoin(AdminRolePermission, AdminRolePermission.role_id == AdminRoleModel.id)
            .outerjoin(AdminPermission, AdminPermission.id == AdminRolePermission.permission_id)
            .where(AdminUserRole.admin_user_id.in_(ids))
        )
        roles: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, bool, set[str]]] = {}
        for admin_id, role_id, name, is_super, code in result.all():
            entry = roles.setdefault((admin_id, role_id), (name, bool(is_super), set()))
            if code:
                entry[2].add(code)
        grouped: dict[uuid.UUID, list[RoleGrant]] = {admin_id: [] for admin_id in ids}
        for (admin_id, _), (name, is_super, codes) in roles.items():
            grouped[admin_id].append(RoleGrant(name=name, is_super=is_super, permissions=frozenset(codes)))
        return grouped

    async def set_admin_roles(
        self, admin_id: uuid.UUID, role_ids: set[uuid.UUID], assigned_by: Optional[uuid.UUID]
    ) -> None:
        result = await self.db.execute(select(AdminUserRole.role_id).where(AdminUserRole.admin_user_id == admin_id))
        current = {row[0] for row in result.all()}
        removed = current - role_ids
        if removed:
            await self.db.execute(delete(AdminUserRole).where(
                AdminUserRole.admin_user_id == admin_id, AdminUserRole.role_id.in_(removed)))
        for role_id in role_ids - current:
            self.db.add(AdminUserRole(admin_user_id=admin_id, role_id=role_id, assigned_by=assigned_by))
        await self.db.flush()

    async def count_active_super_admins(self, *, excluding_admin_id: Optional[uuid.UUID] = None) -> int:
        stmt = (
            select(func.count(func.distinct(AdminUser.id)))
            .select_from(AdminUser)
            .join(AdminUserRole, AdminUserRole.admin_user_id == AdminUser.id)
            .join(AdminRoleModel, AdminRoleModel.id == AdminUserRole.role_id)
            .where(AdminUser.is_active.is_(True), AdminRoleModel.is_super.is_(True))
        )
        if excluding_admin_id is not None:
            stmt = stmt.where(AdminUser.id != excluding_admin_id)
        return int((await self.db.execute(stmt)).scalar_one())
