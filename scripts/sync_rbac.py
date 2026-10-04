"""
Bring the RBAC catalog in the database in step with the code.

    python -m scripts.sync_rbac

Idempotent. Inserts permissions the code now checks, creates any missing system
role, and grants a NEW permission to the system roles that default to it. It never
changes an existing role's permissions, so a Super Admin's edits are kept.
(The API also runs this at start-up.)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.modules.admin.rbac_sync import sync_rbac_catalog  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        summary = await sync_rbac_catalog(db)
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
