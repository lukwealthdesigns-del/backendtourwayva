"""Analytics module - IMPLEMENTED (Phase 8, partial).
See service.py (AnalyticsService: record_ai_usage - wired into
CompanionService after every LLM call - and a generic record_event
for other event types). Cost estimation uses a small static
$/1K-token table, not live pricing.

NOT yet implemented: provider-usage tracking (Amadeus/Brevo/etc. call
counts and latency) and revenue analytics (MRR, churn, conversion) -
both listed in Blueprint section 57 but not built; only the AI-usage
slice is."""
