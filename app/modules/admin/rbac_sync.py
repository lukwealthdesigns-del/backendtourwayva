"""
Keep the RBAC catalog in the database in step with the code
(app/core/admin_permissions.py). Idempotent and safe under concurrent
starts (INSERT ... ON CONFLICT DO NOTHING).

  * every catalog permission exists (descriptions are refreshed);
  * every system role exists (created from its defaults if missing);
  * a permission that is NEW to the database is granted to the system roles that
    default to it — but an existing role's permissions are never touched, so a
    Super Admin's edits (e.g. removing a permission from `support_admin`) survive
    every later sync.

Run at application start-up and via `python -m scripts.sync_rbac`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import PERMISSION_CATALOG, SYSTEM_ROLES, new_permissions_for_system_roles
from app.core.logging import get_logger
from app.db.models.admin import AdminPermission, AdminRoleModel
from app.repositories.rbac_repository import RbacRepository

logger = get_logger(__name__)


async def sync_rbac_catalog(db: AsyncSession) -> dict[str, Any]:
    repo = RbacRepository(db)
    new_codes: list[str] = []

    for definition in PERMISSION_CATALOG:
        inserted = await db.execute(
            pg_insert(AdminPermission)
            .values(code=definition.code, description=definition.description)
            .on_conflict_do_nothing(index_elements=["code"])
            .returning(AdminPermission.id)
        )
        if inserted.scalar_one_or_none() is not None:
            new_codes.append(definition.code)
        else:
            await db.execute(
                update(AdminPermission).where(AdminPermission.code == definition.code)
                .values(description=definition.description)
            )

    created_roles: list[str] = []
    for role in SYSTEM_ROLES:
        inserted = await db.execute(
            pg_insert(AdminRoleModel)
            .values(name=role.name, description=role.description, is_system=True, is_super=role.is_super)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(AdminRoleModel.id)
        )
        role_id = inserted.scalar_one_or_none()
        if role_id is not None:                                  # brand-new role: seed its defaults
            created_roles.append(role.name)
            ids = await repo.permission_ids_by_code(role.permissions)
            await repo.add_role_permissions(role_id, ids.values())

    granted: dict[str, list[str]] = {}
    for role_name, codes in new_permissions_for_system_roles(new_codes).items():
        if role_name in created_roles:
            continue                                             # already got its defaults above
        role = (await db.execute(select(AdminRoleModel).where(AdminRoleModel.name == role_name))).scalar_one_or_none()
        if role is None:
            continue
        ids = await repo.permission_ids_by_code(codes)
        await repo.add_role_permissions(role.id, ids.values())
        granted[role_name] = sorted(codes)

    await db.commit()
    summary = {"new_permissions": new_codes, "created_roles": created_roles, "granted_to_existing_roles": granted}
    if new_codes or created_roles:
        logger.info("rbac_catalog_synced", **{k: v for k, v in summary.items()})
    return summary
