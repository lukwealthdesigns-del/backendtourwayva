"""Aggregates every module router under a single /api/v1 router."""
from fastapi import APIRouter

from app.api.routers.activities import router as activities_router
from app.api.routers.admin import router as admin_router
from app.api.routers.attachments import router as attachments_router
from app.api.routers.auth import router as auth_router
from app.api.routers.collaboration import router as collaboration_router
from app.api.routers.companion import router as companion_router
from app.api.routers.currency import router as currency_router
from app.api.routers.discover import router as discover_router
from app.api.routers.entitlements import router as entitlements_router
from app.api.routers.flights import router as flights_router
from app.api.routers.hotels import router as hotels_router
from app.api.routers.images import router as images_router
from app.api.routers.location import router as location_router
from app.api.routers.maps import router as maps_router
from app.api.routers.memory import router as memory_router
from app.api.routers.notifications import router as notifications_router
from app.api.routers.payments import router as payments_router
from app.api.routers.planning import router as planning_router
from app.api.routers.places import router as places_router
from app.api.routers.saved_places import router as saved_places_router
from app.api.routers.rag import router as rag_router
from app.api.routers.subscriptions import router as subscriptions_router
from app.api.routers.trials import router as trials_router
from app.api.routers.trip_extras import router as trip_extras_router
from app.api.routers.trips import router as trips_router
from app.api.routers.travel_history import router as travel_history_router
from app.api.routers.uploads import router as uploads_router
from app.api.routers.users import router as users_router
from app.api.routers.weather import router as weather_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(uploads_router)
api_v1_router.include_router(attachments_router)

# --- Phase 2: Core Travel ---
api_v1_router.include_router(location_router)
api_v1_router.include_router(currency_router)
api_v1_router.include_router(discover_router)
api_v1_router.include_router(weather_router)
api_v1_router.include_router(images_router)
api_v1_router.include_router(maps_router)
api_v1_router.include_router(hotels_router)
api_v1_router.include_router(flights_router)
api_v1_router.include_router(activities_router)
api_v1_router.include_router(places_router)
api_v1_router.include_router(saved_places_router)

# --- Phase 3: Planning ---
api_v1_router.include_router(trips_router)
api_v1_router.include_router(trip_extras_router)
api_v1_router.include_router(travel_history_router)

# --- Phase 4: AI (partial — see module docstrings for scope) ---
api_v1_router.include_router(memory_router)
api_v1_router.include_router(rag_router)
api_v1_router.include_router(companion_router)

# --- Phase 5: Collaboration ---
api_v1_router.include_router(collaboration_router)

# --- Phase 6: Monetization ---
api_v1_router.include_router(subscriptions_router)
api_v1_router.include_router(payments_router)
api_v1_router.include_router(planning_router)
api_v1_router.include_router(trials_router)
api_v1_router.include_router(entitlements_router)

# --- Phase 7: Admin ---
api_v1_router.include_router(admin_router)

# --- Phase 8: Notifications, Security, Analytics ---
api_v1_router.include_router(notifications_router)

# As later phases are implemented, their routers are included here too:
#   from app.api.routers.discover import router as discover_router
#   from app.api.routers.trips import router as trips_router
#   from app.api.routers.companion import router as companion_router
#   from app.api.routers.admin import router as admin_router
#   ... etc.
