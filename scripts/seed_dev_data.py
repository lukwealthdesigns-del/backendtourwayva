"""
Development seed script (Master Blueprint §94).

Creates, if they don't already exist:
  - One SUPER_ADMIN account (from SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD
    env vars, defaulting to a clearly-fake local-dev address so no one
    accidentally seeds a real inbox)
  - Two plans: "Free" and "Premium"
  - A few sample reference places

Explicitly does NOT seed fake production users — this script is meant
for local/dev/staging databases only. Run it with:

    python -m scripts.seed_dev_data

from the project root (with DATABASE_URL pointed at a dev database —
double-check this before running against anything else).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.admin_permissions import SUPER_ADMIN_ROLE_NAME  # noqa: E402
from app.core.constants import (  # noqa: E402
    AuthProvider,
    BillingInterval,
    FeatureFlag,
    PlaceCategory,
    UserStatus,
)
from app.core.security import hash_password  # noqa: E402
from app.db.models.admin import AdminUser  # noqa: E402
from app.db.models.monetization import Plan  # noqa: E402
from app.db.models.place import Place  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.repositories.admin_repository import AdminRepository  # noqa: E402
from app.repositories.monetization_repository import MonetizationRepository  # noqa: E402
from app.repositories.rbac_repository import RbacRepository  # noqa: E402
from app.repositories.place_repository import PlaceRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.utils.otp_generator import generate_wayva_id  # noqa: E402

SEED_ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@tourwayva.local")
SEED_ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "ChangeMe123!")


async def seed_admin(db) -> User:
    """A local-dev Super Admin with a real password hash (the account can actually log in)."""
    from app.modules.admin.rbac_sync import sync_rbac_catalog

    await sync_rbac_catalog(db)                       # permissions + system roles exist

    user_repo = UserRepository(db)
    admin_repo = AdminRepository(db)
    rbac = RbacRepository(db)

    user = await user_repo.get_by_email(SEED_ADMIN_EMAIL)
    if user is None:
        user = User(
            wayva_id=generate_wayva_id(),
            username="seedadmin",
            first_name="Admin",
            last_name="Admin",
            email=SEED_ADMIN_EMAIL,
            phone_number_e164="+15555550100",
            phone_country_code="234",
            phone_region_code="NGN",
            password_hash=hash_password(SEED_ADMIN_PASSWORD),
            auth_provider=AuthProvider.EMAIL,
            status=UserStatus.ACTIVE,
            is_active=True,
            country="US",
            currency="USD",
            language="en",
            timezone="UTC",
            email_verified_at=datetime.now(timezone.utc),
        )
        await user_repo.create(user)
        print(f"Created seed user: {SEED_ADMIN_EMAIL}")
    else:
        print(f"Seed user already exists: {SEED_ADMIN_EMAIL}")

    admin = await admin_repo.get_admin_by_user_id(user.id)
    if admin is None:
        admin = await admin_repo.create_admin(AdminUser(user_id=user.id, is_active=True, created_by=None))
    super_role = (await rbac.get_roles_by_names([SUPER_ADMIN_ROLE_NAME]))[0]
    await rbac.set_admin_roles(admin.id, {super_role.id}, None)
    print("Granted the super_admin role.")
    return user


async def seed_plans(db) -> None:
    repo = MonetizationRepository(db)

    if await repo.get_plan_by_slug("free") is None:
        await repo.create_plan(
            Plan(
                name="Free", slug="free", price_amount=0, price_currency="USD",
                billing_interval=BillingInterval.FREE,
                included_feature_flags=[FeatureFlag.DISCOVER.value, FeatureFlag.PLANNER.value, FeatureFlag.WEATHER.value],
                is_active=True,
            )
        )
        print("Created plan: Free")

    if await repo.get_plan_by_slug("premium") is None:
        await repo.create_plan(
            Plan(
                name="Premium", slug="premium", price_amount=9.99, price_currency="USD",
                billing_interval=BillingInterval.MONTHLY,
                included_feature_flags=[f.value for f in FeatureFlag],
                is_active=True,
            )
        )
        print("Created plan: Premium")


async def seed_places(db) -> None:
    repo = PlaceRepository(db)

    sample_places = [
        ("Eiffel Tower", PlaceCategory.LANDMARK, "Paris", "FR", 48.8584, 2.2945),
        ("Louvre Museum", PlaceCategory.MUSEUM, "Paris", "FR", 48.8606, 2.3376),
        ("Central Park", PlaceCategory.PARK, "New York", "US", 40.7829, -73.9654),
    ]

    for name, category, city, country, lat, lon in sample_places:
        existing = await repo.search(query=name, limit=1)
        if existing:
            continue
        await repo.create(
            Place(name=name, category=category, city=city, country=country, latitude=lat, longitude=lon, source="seed")
        )
        print(f"Created place: {name}")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await seed_admin(db)
        await seed_plans(db)
        await seed_places(db)
        await db.commit()
    print("\nSeeding complete.")
    print(f"Admin login: {SEED_ADMIN_EMAIL} / {SEED_ADMIN_PASSWORD}")
    print("Change the seed admin password immediately in any shared environment.")


if __name__ == "__main__":
    asyncio.run(main())
