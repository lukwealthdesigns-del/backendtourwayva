"""
Provider Resolver: which providers can answer a capability, in what order.

    Travel service -> ProviderResolver -> provider adapters -> external APIs

Services never name a vendor. They ask for a capability (e.g. FLIGHT_SEARCH) and receive the configured, available
providers in priority order; the first that succeeds wins. Adding a vendor means registering one adapter, not changing a
service, the planner or the Companion. Stdlib only; the settings binding lives in registry.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class Capability(str, Enum):
    HOTEL_DISCOVERY = "hotel_discovery"       # which hotels exist, where, photos, ratings (NOT rooms or prices)
    HOTEL_INVENTORY = "hotel_inventory"       # live rooms and date-specific prices
    ACTIVITY_DISCOVERY = "activity_discovery"
    FLIGHT_SEARCH = "flight_search"
    FLIGHT_STATUS = "flight_status"
    ROUTING = "routing"


@dataclass(frozen=True)
class Registration:
    name: str
    is_configured: Callable[[], bool]
    factory: Callable[[], Any]


def parse_order(raw: str) -> list[str]:
    """'google_places, amadeus' -> ['google_places', 'amadeus'] (lower-case, blanks and repeats dropped)."""
    seen: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


class ProviderResolver:
    def __init__(self, order_for: Callable[[Capability], str]):
        self._order_for = order_for
        self._registry: dict[Capability, dict[str, Registration]] = {}
        self._instances: dict[tuple[Capability, str], Any] = {}

    def register(self, capability: Capability, name: str, *, is_configured: Callable[[], bool], factory: Callable[[], Any]) -> None:
        self._registry.setdefault(capability, {})[name.lower()] = Registration(name.lower(), is_configured, factory)

    def names(self, capability: Capability) -> list[str]:
        """Configured provider names for a capability, in priority order."""
        registered = self._registry.get(capability, {})
        out: list[str] = []
        for name in parse_order(self._order_for(capability)):
            reg = registered.get(name)
            if reg is not None and reg.is_configured():
                out.append(name)
        return out

    def providers(self, capability: Capability) -> list[tuple[str, Any]]:
        out = []
        for name in self.names(capability):
            key = (capability, name)
            if key not in self._instances:
                self._instances[key] = self._registry[capability][name].factory()
            out.append((name, self._instances[key]))
        return out

    def first(self, capability: Capability) -> Any:
        found = self.providers(capability)
        return found[0][1] if found else None

    def available(self, capability: Capability) -> bool:
        return bool(self.names(capability))
