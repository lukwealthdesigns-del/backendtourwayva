"""Discover module - IMPLEMENTED (Phase 8).
See schemas.py, scoring.py (pure, independently-tested scoring/cost-
breakdown logic - verified by hand-execution in this delivery), and
service.py (DiscoverService: the full Blueprint sections 10-12
pipeline - LLM candidate generation -> geocoding verification (drops
any candidate that doesn't resolve to a real place) -> weather ->
currency-converted cost estimate -> image -> nearby activities ->
scoring -> persistence, with Redis caching).

Every cost figure is explicitly source="estimated" - an AI-generated
total split proportionally into accommodation/food/transport/
activities shares, never claimed as live or provider-sourced pricing
(Blueprint section 12: "Never fabricate live prices")."""
