"""
Companion tool system (Master Blueprint sections 39-40).

This is the piece that turns Companion from "a chatbot with your
notes" into something that can actually look things up. Two rules
are non-negotiable here, straight from the blueprint:

  Section 39: "Never allow the LLM to directly execute arbitrary
  database queries." -> every tool below calls an existing,
  already-validated Service (TripService, HotelService, etc.) - never
  raw SQL, never a bespoke query built from LLM-supplied text.

  Section 40: "Every tool must independently verify: user identity,
  user ownership, trip membership, permissions, feature entitlement.
  The LLM cannot bypass authorization." -> every tool function takes
  a ToolExecutionContext carrying the SERVER-VERIFIED user (from the
  JWT, via get_current_user) - never a user_id the LLM might try to
  pass as an argument. Any trip_id the LLM supplies IS treated as
  untrusted input and is re-checked via
  TripService.get_trip_authorized on every call.

Scope: lookups, PROPOSALS and one non-destructive checkpoint. The model
never edits a trip directly — propose_add/update/delete_trip_item and
propose_itinerary_revision only create a PENDING change that a human must
confirm (see change_service.py). `create_trip_version` saves the CURRENT
itinerary as a restorable checkpoint (append-only, editor access required).
Which tools a request may use is decided by its INTENT (intents.py); the agent
node refuses anything outside that allow-list, and every tool below still
re-authorizes the user itself.

Tool failures never raise past this module for the caller to crash
on - they're translated into {"error": "..."} results that get fed
back to the model, which the system prompt already instructs to be
honest about rather than paper over (see companion/service.py).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.modules.companion.intents import TOOL_FEATURES
from app.db.models.user import User
from app.modules.activities.service import ActivityService
from app.modules.currency.service import CurrencyService
from app.modules.flights.service import FlightService
from app.modules.geocoding.service import GeocodingService
from app.modules.hotels.service import HotelService
from app.modules.itinerary.service import ItineraryService
from app.modules.memory.service import MemoryService
from app.modules.places.service import PlaceService
from app.modules.rag.retrieval_service import RAGRetrievalService
from app.modules.trips.service import TripService
from app.modules.weather.service import WeatherService


@dataclass(frozen=True)
class ToolExecutionContext:
    db: AsyncSession
    user: User
    conversation_id: uuid.UUID
    llm: Any = None   # injected LLMProvider for tools that call the model (revisions); None => default provider


# --- OpenAI function-calling tool schemas ---

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_trip_history",
            "description": "Look up the user's past completed trips, optionally filtered to a destination (e.g. 'What was my last trip to France?').",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "Optional destination filter"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_profile",
            "description": "Get the current user's own profile (name, country, currency, language).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_memories",
            "description": "Get facts Tour-Wayva has remembered about the current user's travel preferences.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trip",
            "description": "Get details of one of the current user's trips by ID.",
            "parameters": {
                "type": "object",
                "properties": {"trip_id": {"type": "string", "description": "UUID of the trip"}},
                "required": ["trip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trip_itinerary",
            "description": "Get the full day-by-day itinerary (all items) for one of the current user's trips.",
            "parameters": {
                "type": "object",
                "properties": {"trip_id": {"type": "string", "description": "UUID of the trip"}},
                "required": ["trip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search live hotel offers for a city and date range via Amadeus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city_code": {"type": "string", "description": "3-letter IATA city code, e.g. PAR"},
                    "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                    "check_out": {"type": "string", "description": "YYYY-MM-DD"},
                    "adults": {"type": "integer", "default": 1},
                },
                "required": ["city_code", "check_in", "check_out"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search live flight offers via Amadeus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "3-letter IATA airport/city code"},
                    "destination": {"type": "string", "description": "3-letter IATA airport/city code"},
                    "departure_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "return_date": {"type": "string", "description": "YYYY-MM-DD, omit for one-way"},
                    "adults": {"type": "integer", "default": 1},
                },
                "required": ["origin", "destination", "departure_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_activities",
            "description": "Search bookable tours/activities near a coordinate via Amadeus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "radius_km": {"type": "integer", "default": 5},
                },
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions at a coordinate.",
            "parameters": {
                "type": "object",
                "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}},
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "Convert an amount from one currency to another using a live exchange rate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "base": {"type": "string", "description": "3-letter currency code, e.g. USD"},
                    "target": {"type": "string", "description": "3-letter currency code, e.g. NGN"},
                },
                "required": ["amount", "base", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "geocode",
            "description": "Look up coordinates and location details for a place name or address.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Place name or address"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Search Tour-Wayva's saved reference places (attractions, restaurants, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "radius_km": {"type": "number", "default": 5},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_travel_knowledge",
            "description": (
                "Search Tour-Wayva's curated travel knowledge base for general travel guidance "
                "(visa tips, packing advice, destination overviews, etc.) — use this before answering "
                "general travel questions from memory, since it may have more specific or current info."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to search for"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_add_trip_item",
            "description": (
                "Propose adding a new item (activity, hotel, restaurant, etc.) to a day of the "
                "user's trip. This does NOT change the trip yet — it creates a suggestion the user "
                "must explicitly confirm before anything is added. Always tell the user you've "
                "proposed a change and that they need to confirm it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string", "description": "UUID of the trip"},
                    "day_number": {"type": "integer", "description": "1-indexed day of the trip"},
                    "item_type": {
                        "type": "string",
                        "enum": ["hotel", "flight", "activity", "restaurant", "attraction", "transport", "note", "custom"],
                    },
                    "title": {"type": "string"},
                    "location_name": {"type": "string"},
                    "start_time": {"type": "string", "description": "HH:MM:SS, optional"},
                    "end_time": {"type": "string", "description": "HH:MM:SS, optional"},
                    "estimated_cost": {"type": "number"},
                    "currency": {"type": "string", "description": "3-letter currency code"},
                },
                "required": ["trip_id", "day_number", "item_type", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_update_trip_item",
            "description": (
                "Propose changing an existing trip item (e.g. its time, cost, or title). Does NOT "
                "apply the change until the user confirms it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "UUID of the trip item to change"},
                    "title": {"type": "string"},
                    "start_time": {"type": "string", "description": "HH:MM:SS"},
                    "end_time": {"type": "string", "description": "HH:MM:SS"},
                    "estimated_cost": {"type": "number"},
                    "currency": {"type": "string"},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_delete_trip_item",
            "description": (
                "Propose removing an item from the user's trip. Does NOT delete it until the user "
                "confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": {"item_id": {"type": "string", "description": "UUID of the trip item to remove"}},
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_itinerary_revision",
            "description": (
                "Propose a broader change to a whole trip from a plain-language request, e.g. 'make it cheaper', "
                "'remove day 3', 'reduce walking', 'make it more relaxed', 'add more museums', 'replace the hotel'. "
                "The revised plan is validated (times, travel, budget) and stored as a PROPOSAL the user must "
                "confirm; nothing changes until they do."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string", "description": "UUID of the trip"},
                    "instruction": {"type": "string", "description": "The user's request, in their own words"},
                },
                "required": ["trip_id", "instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_trip_version",
            "description": "Save the trip's CURRENT itinerary as a named checkpoint version the user can restore later. Changes nothing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string"},
                    "label": {"type": "string", "description": "Short name for the checkpoint"},
                },
                "required": ["trip_id", "label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_route",
            "description": "Distance and travel time between two coordinates (use geocode first for place names).",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_latitude": {"type": "number"}, "origin_longitude": {"type": "number"},
                    "destination_latitude": {"type": "number"}, "destination_longitude": {"type": "number"},
                    "mode": {"type": "string", "enum": ["walking", "driving", "cycling", "transit"], "default": "walking"},
                },
                "required": ["origin_latitude", "origin_longitude", "destination_latitude", "destination_longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_destinations",
            "description": (
                "Find destinations that fit a budget and preferences (e.g. 'I have 700,000 NGN, where can I go for "
                "7 days?'). Returns ranked destinations with estimated costs, weather and reasons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_amount": {"type": "number"},
                    "budget_currency": {"type": "string", "description": "3-letter code, e.g. NGN"},
                    "duration_days": {"type": "integer", "default": 7},
                    "travelers": {"type": "integer", "default": 1},
                    "origin": {"type": "string"},
                    "travel_style": {"type": "string"},
                    "interests": {"type": "array", "items": {"type": "string"}},
                    "climate_preference": {"type": "string"},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["budget_amount", "budget_currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_saved_places",
            "description": "List the user's saved (bookmarked) places, optionally filtered to one trip.",
            "parameters": {
                "type": "object",
                "properties": {"trip_id": {"type": "string", "description": "Optional UUID of a trip to filter by"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_place",
            "description": "Bookmark a place (destination, hotel, activity) for the user to find later. Idempotent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": ["attraction", "restaurant", "landmark", "museum", "park",
                                                            "shopping", "nightlife", "other"]},
                    "latitude": {"type": "number"}, "longitude": {"type": "number"},
                    "city": {"type": "string"}, "country": {"type": "string", "description": "2-letter ISO code"},
                    "notes": {"type": "string"}, "trip_id": {"type": "string", "description": "Optional trip UUID"},
                },
                "required": ["name", "latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_hotel",
            "description": (
                "Swap a trip's hotel for a SPECIFIC hotel found via search_hotels (use its hotel_id). "
                "Fetches a fresh price and recalculates cost immediately — no confirmation needed, unlike "
                "propose_itinerary_revision, since this is picking a concrete search result, not an open-ended change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string"},
                    "item_id": {"type": "string", "description": "The existing hotel trip item's id (from get_trip_itinerary)"},
                    "new_hotel_id": {"type": "string", "description": "hotel_id from a search_hotels result"},
                },
                "required": ["trip_id", "item_id", "new_hotel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_flight_to_trip",
            "description": (
                "Add a SPECIFIC flight offer (from search_flights) to a trip day as a verified item. "
                "Pass the offer's fields exactly as search_flights returned them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string"},
                    "day_id": {"type": "string", "description": "Which trip day this flight departs on"},
                    "offer_id": {"type": "string"}, "origin": {"type": "string"}, "destination": {"type": "string"},
                    "departure_time": {"type": "string", "description": "ISO 8601"},
                    "arrival_time": {"type": "string", "description": "ISO 8601"},
                    "duration_iso8601": {"type": "string"}, "stops": {"type": "integer"},
                    "airline_codes": {"type": "array", "items": {"type": "string"}},
                    "price_total": {"type": "number"}, "currency": {"type": "string"}, "provider": {"type": "string"},
                    "cabin": {"type": "string"},
                },
                "required": ["trip_id", "day_id", "offer_id", "origin", "destination", "departure_time",
                            "arrival_time", "duration_iso8601", "stops", "airline_codes", "price_total",
                            "currency", "provider"],
            },
        },
    },
]


def _parse_uuid(value: Any, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(f"'{field_name}' is not a valid ID.") from exc


def _parse_date(value: Optional[str], field_name: str) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(f"'{field_name}' must be in YYYY-MM-DD format.") from exc


async def _get_my_profile(args: dict, ctx: ToolExecutionContext) -> dict:
    u = ctx.user
    return {
        "first_name": u.first_name,
        "last_name": u.last_name,
        "username": u.username,
        "country": u.country,
        "currency": u.currency,
        "language": u.language,
        "timezone": u.timezone,
    }


async def _get_my_memories(args: dict, ctx: ToolExecutionContext) -> dict:
    memories = await MemoryService(ctx.db).list_enabled_for_user(ctx.user.id)
    return {"memories": [m.content for m in memories]}


async def _get_trip(args: dict, ctx: ToolExecutionContext) -> dict:
    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    # Re-verified here regardless of what the model claims - this is
    # the section-40 checkpoint. ctx.user.id comes from the JWT, never the LLM.
    trip = await TripService(ctx.db).get_trip_authorized(trip_id=trip_id, user_id=ctx.user.id)
    return {
        "title": trip.title,
        "destination": trip.destination,
        "origin": trip.origin,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "travelers": trip.travelers,
        "budget_amount": trip.budget_amount,
        "budget_currency": trip.budget_currency,
        "status": trip.status.value,
    }


async def _get_trip_itinerary(args: dict, ctx: ToolExecutionContext) -> dict:
    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    await TripService(ctx.db).get_trip_authorized(trip_id=trip_id, user_id=ctx.user.id)
    days = await ItineraryService(ctx.db).get_full_itinerary(trip_id)
    return {
        "days": [
            {
                "day_number": d.day_number,
                "date": d.date.isoformat(),
                "items": [
                    {
                        "title": i.title,
                        "type": i.item_type.value,
                        "location": i.location_name,
                        "start_time": i.start_time.isoformat() if i.start_time else None,
                        "end_time": i.end_time.isoformat() if i.end_time else None,
                        "estimated_cost": i.estimated_cost,
                        "currency": i.currency,
                    }
                    for i in getattr(d, "items", [])
                ],
            }
            for d in days
        ]
    }


async def _search_hotels(args: dict, ctx: ToolExecutionContext) -> dict:
    check_in = _parse_date(args.get("check_in"), "check_in")
    check_out = _parse_date(args.get("check_out"), "check_out")
    if check_in is None or check_out is None:
        raise AppError("check_in and check_out are required.")

    result = await HotelService().search(
        city_code=str(args.get("city_code", "")),
        check_in=check_in,
        check_out=check_out,
        adults=int(args.get("adults", 1)),
    )
    return result.model_dump(mode="json")


async def _search_flights(args: dict, ctx: ToolExecutionContext) -> dict:
    departure_date = _parse_date(args.get("departure_date"), "departure_date")
    if departure_date is None:
        raise AppError("departure_date is required.")
    return_date = _parse_date(args.get("return_date"), "return_date")

    result = await FlightService().search(
        origin=str(args.get("origin", "")),
        destination=str(args.get("destination", "")),
        departure_date=departure_date,
        return_date=return_date,
        adults=int(args.get("adults", 1)),
    )
    return result.model_dump(mode="json")


async def _search_activities(args: dict, ctx: ToolExecutionContext) -> dict:
    if "latitude" not in args or "longitude" not in args:
        raise AppError("latitude and longitude are required.")
    result = await ActivityService().search(
        latitude=float(args["latitude"]), longitude=float(args["longitude"]),
        radius_km=int(args.get("radius_km", 5)),
    )
    return result.model_dump(mode="json")


async def _get_weather(args: dict, ctx: ToolExecutionContext) -> dict:
    if "latitude" not in args or "longitude" not in args:
        raise AppError("latitude and longitude are required.")
    result = await WeatherService().get_current(latitude=float(args["latitude"]), longitude=float(args["longitude"]))
    return result.model_dump(mode="json")


async def _convert_currency(args: dict, ctx: ToolExecutionContext) -> dict:
    for field_name in ("amount", "base", "target"):
        if field_name not in args:
            raise AppError(f"'{field_name}' is required.")
    result = await CurrencyService().convert(
        amount=float(args["amount"]), base=str(args["base"]), target=str(args["target"])
    )
    return result.model_dump(mode="json")


async def _geocode(args: dict, ctx: ToolExecutionContext) -> dict:
    query = args.get("query")
    if not query:
        raise AppError("'query' is required.")
    result = await GeocodingService().forward_geocode(str(query))
    return result.model_dump(mode="json")


async def _search_places(args: dict, ctx: ToolExecutionContext) -> dict:
    result = await PlaceService(ctx.db).search(
        query=args.get("query"),
        category=args.get("category"),
        latitude=args.get("latitude"),
        longitude=args.get("longitude"),
        radius_km=args.get("radius_km"),
    )
    return result.model_dump(mode="json")


async def _search_travel_knowledge(args: dict, ctx: ToolExecutionContext) -> dict:
    query = args.get("query")
    if not query:
        raise AppError("'query' is required.")
    # Shared knowledge base only (Blueprint §34) — no user-private
    # data is ever retrievable through this tool.
    result = await RAGRetrievalService(ctx.db).search(str(query), top_k=3)
    if not result.results:
        return {"results": [], "note": "No matching knowledge base entries found."}
    return result.model_dump(mode="json")


async def _propose_add_trip_item(args: dict, ctx: ToolExecutionContext) -> dict:
    # Local import avoids a module-level circular import between
    # companion.tools and companion.change_service.
    from app.modules.companion.change_service import PendingChangeService

    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    day_number = args.get("day_number")
    title = args.get("title")
    if day_number is None or not title:
        raise AppError("day_number and title are required.")

    item_fields = {
        "item_type": args.get("item_type", "custom"),
        "title": title,
        "location_name": args.get("location_name"),
        "start_time": args.get("start_time"),
        "end_time": args.get("end_time"),
        "estimated_cost": args.get("estimated_cost"),
        "currency": args.get("currency"),
    }
    change = await PendingChangeService(ctx.db).propose_add_item(
        conversation_id=ctx.conversation_id, trip_id=trip_id, proposer_id=ctx.user.id,
        day_number=int(day_number), item_fields=item_fields,
    )
    return {
        "change_id": str(change.id), "status": "pending_confirmation", "summary": change.summary,
        "note": "This is a proposal only. Tell the user to confirm or reject it before it takes effect.",
    }


async def _propose_update_trip_item(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.companion.change_service import PendingChangeService

    item_id = _parse_uuid(args.get("item_id"), "item_id")
    item_fields = {
        k: v
        for k, v in args.items()
        if k != "item_id" and v is not None
    }
    if not item_fields:
        raise AppError("At least one field to update is required.")

    change = await PendingChangeService(ctx.db).propose_update_item(
        conversation_id=ctx.conversation_id, item_id=item_id, proposer_id=ctx.user.id, item_fields=item_fields,
    )
    return {
        "change_id": str(change.id), "status": "pending_confirmation", "summary": change.summary,
        "note": "This is a proposal only. Tell the user to confirm or reject it before it takes effect.",
    }


async def _propose_delete_trip_item(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.companion.change_service import PendingChangeService

    item_id = _parse_uuid(args.get("item_id"), "item_id")
    change = await PendingChangeService(ctx.db).propose_delete_item(
        conversation_id=ctx.conversation_id, item_id=item_id, proposer_id=ctx.user.id,
    )
    return {
        "change_id": str(change.id), "status": "pending_confirmation", "summary": change.summary,
        "note": "This is a proposal only. Tell the user to confirm or reject it before it takes effect.",
    }


async def _get_trip_history(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.travel_history.service import TravelHistoryService

    service = TravelHistoryService(ctx.db)
    destination = args.get("destination")

    if destination:
        try:
            result = await service.find_last_trip_to(user_id=ctx.user.id, destination_query=str(destination))
            return {
                "destination": result.destination,
                "start_date": result.start_date.isoformat(),
                "end_date": result.end_date.isoformat(),
            }
        except AppError as exc:
            return {"error": exc.message}

    history = await service.get_my_history(ctx.user.id)
    return {
        "trips": [
            {"destination": d.destination, "start_date": d.start_date.isoformat(), "end_date": d.end_date.isoformat()}
            for d in history
        ]
    }


async def _propose_itinerary_revision(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.planning.revision_service import RevisionService

    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    result = await RevisionService(ctx.db, llm=ctx.llm).propose(
        conversation_id=ctx.conversation_id, trip_id=trip_id, user=ctx.user, instruction=args.get("instruction"),
    )
    if not result.get("changed"):
        return {"status": "no_change", "message": result.get("message")}
    return {
        "change_id": result["change_id"], "status": "pending_confirmation", "summary": result["summary"],
        "estimated_cost_before": result["estimated_cost_before"], "estimated_cost_after": result["estimated_cost_after"],
        "currency": result["currency"], "warnings": result["warnings"],
        "note": "This is a proposal only. Tell the user what it changes and that they must confirm or reject it "
                "before anything takes effect.",
    }


async def _create_trip_version(args: dict, ctx: ToolExecutionContext) -> dict:
    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    label = str(args.get("label") or "").strip()
    if not label:
        raise AppError("'label' is required.")
    await TripService(ctx.db).get_trip_authorized(trip_id=trip_id, user_id=ctx.user.id, require_editor=True)
    version = await ItineraryService(ctx.db).create_checkpoint(trip_id=trip_id, actor_id=ctx.user.id, label=label)
    return {"version_number": version.version_number, "summary": version.change_summary}


async def _calculate_route(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.maps.service import MapsService
    from app.providers.maps.interface import TravelMode

    try:
        coords = {k: float(args[k]) for k in (
            "origin_latitude", "origin_longitude", "destination_latitude", "destination_longitude")}
        mode = TravelMode(str(args.get("mode") or "walking").lower())
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("Four coordinates and a valid travel mode are required.") from exc
    for key, value in coords.items():
        limit = 90 if key.endswith("latitude") else 180
        if not -limit <= value <= limit:
            raise AppError(f"'{key}' is out of range.")
    result = await MapsService().get_route(
        origin_lat=coords["origin_latitude"], origin_lon=coords["origin_longitude"],
        destination_lat=coords["destination_latitude"], destination_lon=coords["destination_longitude"], mode=mode,
    )
    return result.model_dump(mode="json")


async def _search_destinations(args: dict, ctx: ToolExecutionContext) -> dict:
    from pydantic import ValidationError

    from app.modules.discover.schemas import DiscoverSearchRequest
    from app.modules.discover.service import DiscoverService

    allowed = ("budget_amount", "budget_currency", "duration_days", "travelers", "origin", "travel_style",
               "interests", "climate_preference", "max_results")
    fields = {k: v for k, v in args.items() if k in allowed and v not in (None, "")}
    fields.setdefault("max_results", 3)
    try:
        payload = DiscoverSearchRequest(**fields)
    except ValidationError as exc:
        raise AppError("Invalid destination search: " + "; ".join(e["msg"] for e in exc.errors())) from exc
    response = await DiscoverService(ctx.db, llm=ctx.llm).search(user_id=ctx.user.id, payload=payload)
    return response.model_dump(mode="json")


async def _get_saved_places(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.saved_places.service import SavedPlaceService

    trip_id = _parse_uuid(args["trip_id"], "trip_id") if args.get("trip_id") else None
    places = await SavedPlaceService(ctx.db).list_for_user(ctx.user.id, trip_id=trip_id)
    return {"count": len(places), "places": [
        {"id": str(p.id), "name": p.name, "category": p.category.value, "city": p.city, "country": p.country,
         "latitude": p.latitude, "longitude": p.longitude, "notes": p.notes} for p in places
    ]}


async def _save_place(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.core.constants import PlaceCategory
    from app.modules.saved_places.schemas import SavePlaceRequest
    from app.modules.saved_places.service import SavedPlaceService

    try:
        payload = SavePlaceRequest(
            name=args["name"], category=PlaceCategory(args.get("category", "other")),
            latitude=float(args["latitude"]), longitude=float(args["longitude"]),
            city=args.get("city"), country=args.get("country"), notes=args.get("notes"), source="companion",
            trip_id=_parse_uuid(args["trip_id"], "trip_id") if args.get("trip_id") else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("Invalid place details.") from exc
    result = await SavedPlaceService(ctx.db).save(user_id=ctx.user.id, payload=payload)
    return {"id": str(result.place.id), "name": result.place.name, "was_new": result.was_new}


async def _replace_hotel(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.itinerary.hotel_replacement_service import HotelReplacementService

    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    item_id = _parse_uuid(args.get("item_id"), "item_id")
    new_hotel_id = str(args.get("new_hotel_id") or "").strip()
    if not new_hotel_id:
        raise AppError("'new_hotel_id' is required.")
    result = await HotelReplacementService(ctx.db).replace(
        trip_id=trip_id, item_id=item_id, new_hotel_id=new_hotel_id, user_id=ctx.user.id
    )
    return {
        "item_id": str(result.item_id), "title": result.title, "previous_cost": result.previous_cost,
        "new_cost": result.new_cost, "currency": result.currency, "cost_delta": result.cost_delta,
        "version_number": result.version_number,
    }


async def _add_flight_to_trip(args: dict, ctx: ToolExecutionContext) -> dict:
    from app.modules.itinerary.flight_booking import FlightOffer
    from app.modules.itinerary.flight_booking_service import FlightBookingService

    trip_id = _parse_uuid(args.get("trip_id"), "trip_id")
    day_id = _parse_uuid(args.get("day_id"), "day_id")
    try:
        offer = FlightOffer(
            offer_id=str(args["offer_id"]), origin=str(args["origin"]).upper(), destination=str(args["destination"]).upper(),
            departure_time=str(args["departure_time"]), arrival_time=str(args["arrival_time"]),
            duration_iso8601=str(args["duration_iso8601"]), stops=int(args["stops"]),
            airline_codes=[str(a) for a in args["airline_codes"]], price_total=float(args["price_total"]),
            currency=str(args["currency"]).upper(), provider=str(args["provider"]), cabin=args.get("cabin"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("Invalid flight offer details.") from exc
    result = await FlightBookingService(ctx.db).add_to_trip(trip_id=trip_id, day_id=day_id, offer=offer, user_id=ctx.user.id)
    return {"item_id": str(result.item_id), "title": result.title, "estimated_cost": result.estimated_cost,
           "currency": result.currency, "version_number": result.version_number,
           "price_verified": result.price_verified, "verification_note": result.verification_note}


_HANDLERS = {
    "get_trip_history": _get_trip_history,
    "get_my_profile": _get_my_profile,
    "get_my_memories": _get_my_memories,
    "get_trip": _get_trip,
    "get_trip_itinerary": _get_trip_itinerary,
    "search_hotels": _search_hotels,
    "search_flights": _search_flights,
    "search_activities": _search_activities,
    "get_weather": _get_weather,
    "convert_currency": _convert_currency,
    "geocode": _geocode,
    "search_places": _search_places,
    "search_travel_knowledge": _search_travel_knowledge,
    "propose_add_trip_item": _propose_add_trip_item,
    "propose_update_trip_item": _propose_update_trip_item,
    "propose_delete_trip_item": _propose_delete_trip_item,
    "propose_itinerary_revision": _propose_itinerary_revision,
    "create_trip_version": _create_trip_version,
    "calculate_route": _calculate_route,
    "search_destinations": _search_destinations,
    "get_saved_places": _get_saved_places,
    "save_place": _save_place,
    "replace_hotel": _replace_hotel,
    "add_flight_to_trip": _add_flight_to_trip,
}


async def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolExecutionContext) -> dict:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        feature = TOOL_FEATURES.get(name)
        if feature is not None:
            # Authoritative check, independent of what the agent chose to offer: the tool
            # respects the user's plan exactly like the equivalent REST endpoint does.
            from app.modules.entitlements.service import EntitlementService

            await EntitlementService(ctx.db).require(ctx.user.id, feature)
        return await handler(arguments, ctx)
    except AppError as exc:
        # Controlled failure (not found, forbidden, provider down, bad
        # input) -> tell the model plainly rather than crashing the
        # whole Companion turn over one failed lookup.
        return {"error": exc.message}
