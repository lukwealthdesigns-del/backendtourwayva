"""
Static guarantees about how features are enforced (Blueprint §50: "Every feature
flag must be evaluated server-side"). These scan the router source, so a NEW
endpoint that forgets its gate — or an endpoint that is accidentally public —
fails the build.
"""
import ast
import re
from pathlib import Path

from app.core.constants import FeatureFlag
from app.modules.companion.intents import INTENT_TOOLS, TOOL_FEATURES, Intent
from app.modules.companion.tools import _HANDLERS

ROUTERS = Path(__file__).resolve().parents[2] / "app" / "api" / "routers"

# The specification: endpoint function -> feature(s) it requires. Reads of a user's OWN data
# (list trips, view memories, list attachments...) are intentionally not gated: a lapsed plan must
# never lock people out of their data.
EXPECTED_GATES = {
    "discover.py": {"search_destinations": {"DISCOVER"}},
    "activities.py": {"search_activities": {"ACTIVITIES"}},
    "hotels.py": {"search_hotels": {"HOTELS"}},
    "flights.py": {"search_flights": {"FLIGHTS"}},
    "weather.py": {"current_weather": {"WEATHER"}, "destination_weather": {"WEATHER"}},
    "attachments.py": {"upload_attachment": {"ATTACHMENTS"}, "confirm_attachment": {"ATTACHMENTS"},
                       "link_attachment_to_trip": {"ATTACHMENTS"}},
    "uploads.py": {"upload_attachment": {"ATTACHMENTS"}, "upload_travel_document": {"ATTACHMENTS"},
                   "upload_trip_pdf": {"PDF_EXPORT"}},
    "trip_extras.py": {"export_trip_pdf": {"PDF_EXPORT"}, "add_trip_note": {"PLANNER"}, "add_trip_cost": {"PLANNER"},
                       "calculate_trip_route": {"PLANNER"}},
    "trips.py": {"create_trip": {"PLANNER"}, "add_trip_item": {"PLANNER"}, "update_trip_item": {"PLANNER"},
                 "delete_trip_item": {"PLANNER"}, "restore_trip_version": {"PLANNER"}, "replace_hotel": {"PLANNER"},
                 "add_flight": {"PLANNER"}},
    "planning.py": {"generate_itinerary": {"PLANNER"}, "update_trip_preferences": {"PLANNER"}},
    "companion.py": {"create_conversation": {"COMPANION"}, "send_message": {"COMPANION"},
                     "send_voice_message": {"COMPANION", "VOICE"}, "confirm_change": {"PLANNER"}},
    "memory.py": {"create_memory": {"MEMORY"}},
    "collaboration.py": {"invite_member": {"COLLABORATION"}},
}
# Deliberately unauthenticated: the auth flows themselves and the signature-authenticated webhook.
PUBLIC_ENDPOINTS = {
    ("auth.py", name) for name in (
        "signup", "verify_email", "resend_otp", "login", "refresh_token", "logout", "forgot_password",
        "reset_password", "google_sign_in", "google_complete", "username_available",
    )
} | {("payments.py", "paystack_webhook")}
# Provider-backed endpoints that cost real money per call must be rate limited.
RATE_LIMITED = {
    "hotels.py": {"search_hotels"}, "flights.py": {"search_flights"}, "activities.py": {"search_activities"},
    "maps.py": None, "location.py": None, "images.py": None, "weather.py": {"current_weather", "destination_weather"},
    "currency.py": None, "rag.py": {"search_knowledge"}, "discover.py": {"search_destinations"},
    "planning.py": {"generate_itinerary"},
}


def _endpoints(filename):
    tree = ast.parse((ROUTERS / filename).read_text())
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in ("get", "post", "put", "patch", "delete")):
                    yield node, decorator


def _gates(decorator):
    return set(re.findall(r"require_feature\(FeatureFlag\.(\w+)\)", ast.unparse(decorator)))


def test_every_gated_endpoint_requires_exactly_the_specified_features():
    for filename, expected in EXPECTED_GATES.items():
        found = {node.name: _gates(dec) for node, dec in _endpoints(filename)}
        for function, flags in expected.items():
            assert function in found, f"{filename}:{function} not found"
            assert found[function] == flags, f"{filename}:{function} is gated by {found[function]}, expected {flags}"


def test_no_endpoint_outside_the_specification_is_gated_by_accident():
    for path in ROUTERS.glob("*.py"):
        for node, decorator in _endpoints(path.name):
            gates = _gates(decorator)
            if gates:
                assert node.name in EXPECTED_GATES.get(path.name, {}), f"{path.name}:{node.name} has an unlisted gate {gates}"


def test_every_feature_flag_is_enforced_somewhere_except_the_one_with_no_feature_behind_it():
    enforced = set()
    for filename in EXPECTED_GATES:
        for node, decorator in _endpoints(filename):
            enforced |= _gates(decorator)
    enforced |= {flag.value for flag in TOOL_FEATURES.values()}
    enforced.add("PREMIUM_AI")                                # selects the strong planning model (planning/deps.py)
    unenforced = {f.value for f in FeatureFlag} - enforced
    assert unenforced == {"LIVE_TRAVEL"}, unenforced          # nothing implements live-travel features yet


def test_only_the_intended_endpoints_are_public():
    public = set()
    for path in ROUTERS.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree_source = path.read_text()
        router_level_deps = "dependencies=[" in tree_source.split("router = APIRouter", 1)[1].split(")", 1)[0]
        for node, decorator in _endpoints(path.name):
            source = ast.unparse(node)
            authenticated = ("get_current_user" in source or "require_admin_permission" in ast.unparse(decorator)
                             or router_level_deps)
            if not authenticated:
                public.add((path.name, node.name))
    assert public == PUBLIC_ENDPOINTS, sorted(public ^ PUBLIC_ENDPOINTS)


def test_provider_backed_endpoints_are_rate_limited():
    for filename, functions in RATE_LIMITED.items():
        for node, decorator in _endpoints(filename):
            if functions is None or node.name in functions:
                assert "rate_limit(" in ast.unparse(decorator) or "rate_limit(" in (ROUTERS / filename).read_text().split("router = APIRouter", 1)[1].split("\n\n", 1)[0], \
                    f"{filename}:{node.name} is not rate limited"


def test_companion_tools_that_use_a_paid_feature_declare_it():
    assert set(TOOL_FEATURES) <= set(_HANDLERS)
    # every tool that reaches a feature-gated REST capability, and every write-capable tool, needs its feature
    for tool, flag in {"search_hotels": "HOTELS", "search_flights": "FLIGHTS", "search_activities": "ACTIVITIES",
                       "get_weather": "WEATHER", "search_destinations": "DISCOVER", "get_my_memories": "MEMORY"}.items():
        assert TOOL_FEATURES[tool].value == flag
    for tool in INTENT_TOOLS[Intent.ITINERARY_EDIT]:
        if tool.startswith("propose_") or tool == "create_trip_version":
            assert TOOL_FEATURES[tool].value == "PLANNER", tool
