"""Subscriptions module - IMPLEMENTED (Phase 6).
See schemas.py and service.py (SubscriptionService: plan CRUD,
subscribe/cancel state machine).

IMPORTANT: no payment processor is integrated - see
app/db/models/monetization.py's module docstring. subscribe()
activates immediately with no payment collected, because no payment
provider (Stripe, Paystack, etc.) was named in the Master Build
Prompt. Wiring a real one means adding a `payments` provider
(app/providers - not yet built) that triggers this same state
transition from a successful webhook instead of on direct request."""
