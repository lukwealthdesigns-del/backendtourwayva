"""Unit tests for the pure-logic pieces behind the admin cost/revenue
dashboard (Master Blueprint §57-58, §89): commission estimation and the
cache hit-rate category bookkeeping. The DB-backed aggregate queries
(MRR, churn, trial conversion, revenue, ai_cost_by_user/trip) need a real
Postgres and are exercised in tests/integration instead."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/test")

from app.modules.analytics.service import estimate_commission_usd
from app.services.cache_service import (
    ESTIMATED_COST_SAVED_PER_HIT_USD,
    _NON_PROVIDER_CACHE_CATEGORIES,
    _PROVIDER_CACHE_CATEGORIES,
)


def test_estimate_commission_hotel_uses_configured_rate():
    from app.core.config import settings

    commission = estimate_commission_usd(item_type="hotel", estimated_value_usd=1000.0)
    assert commission == round(1000.0 * settings.ANALYTICS_HOTEL_COMMISSION_RATE, 4)


def test_estimate_commission_unknown_item_type_is_zero():
    assert estimate_commission_usd(item_type="note", estimated_value_usd=500.0) == 0.0


def test_estimate_commission_zero_value_is_zero():
    assert estimate_commission_usd(item_type="flight", estimated_value_usd=0.0) == 0.0


def test_estimate_commission_negative_value_is_zero():
    # Defensive: a caller bug should never produce a negative "commission".
    assert estimate_commission_usd(item_type="activity", estimated_value_usd=-50.0) == 0.0


def test_every_provider_cache_category_has_an_estimated_savings_rate():
    # Every category the hit-rate stats will report on must have SOME
    # estimated $/hit, even if small — a silently-missing entry would make
    # that category's "estimated_savings_usd" always compute to 0, which
    # reads as "this cache never saves money" rather than "not configured".
    for category in _PROVIDER_CACHE_CATEGORIES:
        assert category in ESTIMATED_COST_SAVED_PER_HIT_USD
        assert ESTIMATED_COST_SAVED_PER_HIT_USD[category] > 0


def test_provider_and_non_provider_categories_never_overlap():
    assert set(_PROVIDER_CACHE_CATEGORIES).isdisjoint(_NON_PROVIDER_CACHE_CATEGORIES)


def test_amadeus_token_cache_excluded_from_hit_rate_reporting():
    # The Amadeus OAuth token cache (app/providers/amadeus/client.py) uses
    # key "amadeus:access_token" — its hit/miss ratio is about token
    # refresh timing, not "provider calls avoided", so it must not leak
    # into the provider-lookup hit-rate dashboard.
    assert "amadeus" in _NON_PROVIDER_CACHE_CATEGORIES
    assert "amadeus" not in _PROVIDER_CACHE_CATEGORIES
