"""
Bootstrap the FIRST Super Admin in any environment, including production.

    python -m scripts.create_super_admin --email you@example.com

The account must already exist and be verified (sign up normally first). Running
this needs direct access to the database and the app's environment, which is the
point: there is deliberately no API path to become a Super Admin without an
existing Super Admin. The action is written to the audit log.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.admin_permissions import SUPER_ADMIN_ROLE_NAME  # noqa: E402
from app.core.constants import AuditResult  # noqa: E402
from app.db.models.admin import AdminUser  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.modules.admin.admin_service import AdminService  # noqa: E402
from app.modules.admin.rbac_sync import sync_rbac_catalog  # noqa: E402
from app.repositories.admin_repository import AdminRepository  # noqa: E402
from app.repositories.rbac_repository import RbacRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402


async def promote(email: str) -> str:
    async with AsyncSessionLocal() as db:
        await sync_rbac_catalog(db)
        user = await UserRepository(db).get_by_email(email)
        if user is None or not user.is_active:
            return f"No active, verified account for {email}. Sign up and verify the email first."

        repo, rbac = AdminRepository(db), RbacRepository(db)
        admin = await repo.get_admin_by_user_id(user.id)
        if admin is None:
            admin = await repo.create_admin(AdminUser(user_id=user.id, is_active=True, created_by=None))
        admin.is_active = True
        super_role = (await rbac.get_roles_by_names([SUPER_ADMIN_ROLE_NAME]))[0]
        await rbac.set_admin_roles(admin.id, {super_role.id}, None)
        await AdminService(db).log(
            admin_user_id=user.id, action="admin.bootstrap", target_type="user", target_id=str(user.id),
            result=AuditResult.SUCCESS, reason="Promoted to super_admin via scripts.create_super_admin",
        )
        await db.commit()
        return f"{email} is now a Super Admin."


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote an existing verified user to Super Admin.")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    print(asyncio.run(promote(args.email.strip().lower())))


if __name__ == "__main__":
    main()
