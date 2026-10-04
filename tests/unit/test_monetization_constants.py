from app.core.constants import FREE_TIER_DEFAULT_FLAGS, BillingInterval, FeatureFlag


def test_feature_flag_matches_blueprint_list_exactly():
    expected = {
        "DISCOVER", "PLANNER", "COMPANION", "VOICE", "ATTACHMENTS", "MEMORY",
        "HOTELS", "FLIGHTS", "ACTIVITIES", "WEATHER", "LIVE_TRAVEL", "PDF_EXPORT",
        "COLLABORATION", "PREMIUM_AI",
    }
    actual = {f.value for f in FeatureFlag}
    assert actual == expected


def test_free_tier_defaults_are_a_subset_of_all_flags():
    all_flags = set(FeatureFlag)
    assert FREE_TIER_DEFAULT_FLAGS <= all_flags


def test_free_tier_does_not_include_companion_or_premium_ai():
    """Guards the deliberate scope decision that Companion access
    requires a plan or trial, not the free tier."""
    assert FeatureFlag.COMPANION not in FREE_TIER_DEFAULT_FLAGS
    assert FeatureFlag.PREMIUM_AI not in FREE_TIER_DEFAULT_FLAGS


def test_billing_interval_days_mapping_covers_every_interval():
    from app.modules.subscriptions.service import _INTERVAL_DAYS

    for interval in BillingInterval:
        assert interval in _INTERVAL_DAYS
    assert _INTERVAL_DAYS[BillingInterval.MONTHLY] == 30
    assert _INTERVAL_DAYS[BillingInterval.YEARLY] == 365
