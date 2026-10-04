# Tour-Wayva Backend

Production-oriented FastAPI backend for **Tour-Wayva**, an AI-powered
travel intelligence platform (Discover, Planning, Companion).

This delivery implements **Phases 1 through 8** of the Master Build
Prompt, plus four specific signup/upload/auth adjustments layered
onto Phase 1. See the roadmap table further down for exact
per-feature status — the short version: Foundation, Core Travel
(Geocoding/Currency/Weather/Images/Maps/Hotels/Flights/Activities/
Places), Planning (trips + structured itinerary), AI (LLM/Embeddings/
Memory/RAG/Companion with tool-calling), Collaboration
(invitations/roles/comments/voting), Monetization (plans,
subscriptions, trials, entitlements), Admin (RBAC, user management,
messaging, broadcasts, audit log), and Production hardening (rate
limiting, failed-login lockout, security headers, mock providers,
seed script) are built. See "What's still genuinely missing" near the
end of this README for the honest remaining gaps — this is a real,
runnable backend, not a finished product.

The four adjustments layered onto Phase 1:

1. **Image/file upload routing** — Cloudinary for User Avatars and
   Destination Images (optimization + CDN); Supabase Storage for
   Attachments, Trip PDFs, and Travel Documents (RLS + arbitrary file
   types).
2. **OTP-based auth (Brevo)** — email verification and password reset
   use a 6-digit OTP emailed via Brevo. **No verification/reset links
   are ever sent.**
3. **New signup flow** — `first_name`, `last_name`, `username`
   (unique), `phone_number` (validated for every country via
   `phonenumbers`/libphonenumber), `email`, `password`,
   `confirm_password`. Travel preferences are deferred to optional,
   skippable onboarding. `wayva_id`, `country`, `currency`,
   `language`, `timezone`, and `google_profile_picture_url` are
   derived automatically.
4. **Pending activation** — every new user is created with
   `is_active = False` / `status = pending` and is only activated
   once their OTP is verified.

Everything else in the original 111-section Master Build Prompt
(Discover, Planning/Itinerary, Companion/AI, RAG, Hotels/Flights/
Activities, Admin, Billing, etc.) is **scaffolded, not implemented** —
see [Roadmap](#roadmap--whats-scaffolded-vs-implemented) below. Every
scaffolded package says so in its own docstring; nothing pretends to
work that doesn't.

---

## Full folder structure

```
tour-wayva-backend/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
│
├── app/
│   ├── main.py                          # FastAPI app, middleware, exception handlers, health checks
│   │
│   ├── core/                            # ── IMPLEMENTED ──
│   │   ├── config.py                    # Pydantic Settings (all env vars)
│   │   ├── country_data.py              # 196-country currency/language/timezone dataset
│   │   ├── security.py                  # password hashing, JWT issuance/verification
│   │   ├── exceptions.py                # standardized AppError hierarchy
│   │   ├── logging.py                   # structlog config, secret redaction
│   │   ├── constants.py                 # shared enums (UserStatus, OTPPurpose, UploadUseCase, ...)
│   │   ├── admin_permissions.py         # permission CATALOG, system-role defaults, pure access rules
│   │   └── rate_limit.py                # Redis-backed rate limiter + failed-login lockout (Phase 8)
│   │
│   ├── db/                              # ── IMPLEMENTED (foundation) ──
│   │   ├── session.py                   # async engine + get_db() dependency
│   │   ├── base.py                      # Declarative Base, UUIDPKMixin, TimestampMixin
│   │   ├── models/
│   │   │   ├── __init__.py              # imports all models for Alembic autogenerate
│   │   │   ├── user.py                  # User model (new signup fields)
│   │   │   ├── otp.py                   # OTPCode model
│   │   │   ├── session.py               # UserSession (refresh-token rotation, per-device sessions)
│   │   │   ├── payment.py               # Payment, SubscriptionEvent, PaymentWebhookEvent (Paystack)
│   │   │   ├── preferences.py           # UserPreferences (optional onboarding/profile preferences)
│   │   │   ├── trip.py                  # Trip, TripMember, TripDay, TripItem, TripVersion (Phase 3)
│   │   │   ├── place.py                 # Place reference data (Phase 2)
│   │   │   ├── memory.py                # UserMemory (Phase 4)
│   │   │   ├── conversation.py          # Conversation (+ rolling summary), Message (Phase 4)
│   │   │   ├── knowledge.py             # KnowledgeDocument, KnowledgeChunk — pgvector (Phase 4)
│   │   │   ├── pending_change.py        # PendingItineraryChange — AI propose/confirm/reject (Phase 4)
│   │   │   ├── collaboration.py         # TripInvitation, TripComment, PendingChangeVote (Phase 5)
│   │   │   ├── monetization.py          # Plan, Subscription, TrialConfig, UserTrial, FeatureFlagOverride (Phase 6)
│   │   │   └── admin.py                 # AdminUser, AdminAuditLog, AdminMessage, BroadcastJob (Phase 7)
│   │   └── migrations/                  # Alembic
│   │       ├── env.py
│   │       ├── script.py.mako
│   │       └── versions/
│   │           ├── 0001_initial_users_and_otp.py
│   │           ├── 0002_trips_itinerary.py
│   │           ├── 0003_places.py
│   │           ├── 0004_memory_and_companion.py
│   │           ├── 0005_rag_knowledge.py
│   │           ├── 0006_pending_changes_and_summary.py
│   │           ├── 0007_collaboration.py
│   │           ├── 0008_monetization.py
│   │           ├── 0009_admin.py
│   │           ├── ...                      # 0010-0012: security/notifications/analytics, attachments/travel history, discover
│   │           ├── 0013_sessions_preferences_account_deletion.py   # user_sessions, user_preferences, account_deletion OTP purpose
│   │           ├── 0014_payments_paystack.py                      # payments, subscription_events, payment_webhook_events, plan/subscription columns
│   │           ├── 0015_planning.py                               # trip overview, item provenance columns, trip_preferences
│   │           ├── 0016_pending_change_revise.py                  # 'revise_trip' pending-change action
│   │           ├── 0017_admin_rbac.py                             # RBAC tables + data migration + audit-log trigger
│   │           ├── 0018_feature_flags.py                          # global feature-flag toggles
│   │           ├── 0019_workers.py                                # lifecycle markers, ingestion status, daily_metrics
│   │           ├── 0020_usage_and_durable_cache.py                # provider_usage, api_usage, geocoding_cache, currency_cache
│   │           └── 0021_row_level_security.py                     # RLS policies (defense in depth, non-breaking by default)
│   │
│   ├── api/
│   │   ├── dependencies.py              # get_current_user (JWT-verified), get_client_ip
│   │   └── routers/
│   │       ├── __init__.py              # aggregates all routers under api_v1_router
│   │       ├── auth.py                  # signup, verify-email, login, refresh (rotating), logout(-all), change/forgot/reset-password, Google, username-available
│   │       ├── users.py                 # /users/me: profile, avatar, preferences, onboarding, sessions, export, account deletion
│   │       ├── uploads.py               # /uploads/avatar, /attachment, /trip-pdf/{id}, /travel-document
│   │       ├── attachments.py           # Phase 8: OCR/extraction pipeline
│   │       ├── location.py, currency.py, weather.py, images.py, maps.py    # Phase 2
│   │       ├── hotels.py, flights.py, activities.py                        # Phase 2 (Amadeus)
│   │       ├── places.py                # Phase 2
│   │       ├── discover.py              # Phase 8
│   │       ├── trips.py                 # Phase 3: trips, itinerary, versions
│   │       ├── trip_extras.py           # Phase 8: notes, costs, routes, PDF export
│   │       ├── travel_history.py        # Phase 8
│   │       ├── memory.py                # Phase 4
│   │       ├── rag.py                   # Phase 4
│   │       ├── companion.py             # Phase 4 (+ Phase 8 voice-messages endpoint)
│   │       ├── collaboration.py         # Phase 5: invitations, members, comments, votes
│   │       ├── subscriptions.py         # Phase 6
│   │       ├── trials.py                # Phase 6
│   │       ├── entitlements.py          # Phase 6
│   │       ├── admin.py                 # Phase 7 (+ Phase 8 security/analytics endpoints)
│   │       └── notifications.py         # Phase 8
│   │
│   ├── modules/
│   │   ├── auth/                        # ── IMPLEMENTED ──
│   │   │   ├── schemas.py               # SignupRequest, LoginRequest, VerifyOTPRequest, ...
│   │   │   ├── service.py               # AuthService (signup/verify/login/reset/change-password/sessions/Google)
│   │   │   └── otp_service.py           # OTPService (issue, send via Brevo, verify; attempts persisted before raising)
│   │   ├── saved_places/                # ── IMPLEMENTED ── personal bookmark list (free tier)
│   │   ├── discover/                    # ── IMPLEMENTED ── destination discovery (LangGraph)
│   │   │   ├── logic.py                 # pure: constraints, cache key, candidate parsing, season fit, weather
│   │   │   ├── scoring.py               # pure: cost breakdown + budget/interest/season scoring
│   │   │   ├── graph.py                 # the Discover workflow (nodes + routing)
│   │   │   ├── service.py               # DiscoverService: real ports, persistence, error mapping
│   │   │   └── schemas.py
│   │   ├── companion/                   # ── IMPLEMENTED ── LangGraph turn: intent routing, gating, tools
│   │   │   ├── intents.py               # intents, rule classifier, tool allow-lists, feature map
│   │   │   ├── context.py               # prompt/context assembly (only what the intent needs)
│   │   │   ├── graph.py                 # classify_intent → check_access → load_context → agent | deny
│   │   │   ├── service.py, tools.py, change_service.py, ...
│   │   ├── lifecycle/                   # ── IMPLEMENTED ── scheduled maintenance
│   │   │   ├── service.py               # trial/subscription reminders + expiry
│   │   │   ├── retention.py             # delete expired personal/operational data
│   │   │   ├── metrics.py               # nightly daily_metrics aggregation
│   │   │   └── cache_warming.py         # keep exchange rates warm
│   │   ├── planning/                    # ── IMPLEMENTED ── AI itinerary generation + revision (LangGraph)
│   │   │   ├── domain.py                # plain dataclasses (no framework imports)
│   │   │   ├── rules.py                 # pure validation + deterministic repair
│   │   │   ├── llm_io.py                # prompts + strict parsing of model output
│   │   │   ├── workflow.py              # WorkflowSpec, LangGraph builder, local runner (tests)
│   │   │   ├── graph.py                 # the generation workflow (nodes + routing)
│   │   │   ├── deps.py                  # real ports: geocoding, Amadeus, weather, currency, LLM, DB
│   │   │   ├── persistence.py           # atomic write + version snapshot
│   │   │   ├── revision.py              # conversational revision: plan<->DB, diff, workflow spec
│   │   │   ├── revision_service.py      # RevisionService: validated PENDING proposals
│   │   │   ├── service.py               # PlanningService: auth, jobs, idempotency, locking
│   │   │   ├── jobs.py                  # Redis job state / idempotency / per-trip lock
│   │   │   └── schemas.py
│   │   ├── users/                       # ── IMPLEMENTED ──
│   │   │   ├── schemas.py               # ProfileUpdateRequest (extra=forbid), Preferences*, SessionResponse, ...
│   │   │   ├── service.py               # UserService: profile, preferences, onboarding, sessions
│   │   │   └── account_service.py       # AccountService: data export + OTP-confirmed anonymizing deletion
│   │   ├── uploads/                     # ── IMPLEMENTED ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # validation + delegates to storage factory
│   │   ├── geocoding/                   # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # GeocodingService (cache-first, OpenCage)
│   │   ├── currency/                    # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # CurrencyService (cache-first, CurrencyAPI)
│   │   ├── weather/                     # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # WeatherService (cache-first, WeatherAPI.com)
│   │   ├── images/                      # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # ImageService (normalized-key cache, Unsplash)
│   │   ├── maps/                        # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # MapsService (cache-first routing, OpenRouteService)
│   │   ├── hotels/                      # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # HotelService (5-min-TTL cache, AmadeusHotelProvider)
│   │   ├── flights/                     # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # FlightService (3-min-TTL cache, AmadeusFlightProvider)
│   │   ├── activities/                  # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # ActivityService (6-hr-TTL cache, AmadeusActivityProvider)
│   │   ├── trips/                       # ── IMPLEMENTED (Phase 3) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # TripService (creation, day scaffolding, authorization checkpoint)
│   │   ├── itinerary/                   # ── IMPLEMENTED (Phase 3, partial — see roadmap table) ──
│   │   │   ├── schemas.py
│   │   │   ├── service.py               # ItineraryService (item CRUD + versioning/restore)
│   │   │   ├── validation_service.py    # ItineraryValidationService (conflicts/duplicates/etc.)
│   │   │   ├── hotel_replacement.py, hotel_replacement_service.py   # ── IMPLEMENTED ── swap a hotel, recalculated live
│   │   │   └── flight_booking.py, flight_booking_service.py         # ── IMPLEMENTED ── add a verified flight offer
│   │   ├── places/                      # ── IMPLEMENTED (Phase 2, closes it out) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # PlaceService (text/category/radius search)
│   │   ├── memory/                      # ── IMPLEMENTED (Phase 4, partial) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # MemoryService (view/edit/delete/disable)
│   │   ├── companion/                   # ── IMPLEMENTED (Phase 4, partial — see caveats below) ──
│   │   │   ├── schemas.py
│   │   │   ├── service.py               # CompanionService (conversations + bounded tool-calling loop)
│   │   │   ├── tools.py                 # TOOL_SCHEMAS + execute_tool (read-only + propose_*, all re-authorized)
│   │   │   ├── change_service.py        # PendingChangeService (propose -> confirm/reject -> apply)
│   │   │   └── summarization.py         # rolling conversation summary once history grows long
│   │   ├── rag/                         # ── IMPLEMENTED (Phase 4, complete) ──
│   │   │   ├── schemas.py
│   │   │   ├── chunking.py              # paragraph-based text chunker
│   │   │   ├── ingestion_service.py     # RAGIngestionService (chunk -> embed -> store)
│   │   │   └── retrieval_service.py     # RAGRetrievalService (similarity -> filter -> hybrid rerank)
│   │   ├── collaboration/               # ── IMPLEMENTED (Phase 5) ──
│   │   │   ├── schemas.py
│   │   │   ├── invitation_service.py    # InvitationService (invite/accept/reject/cancel)
│   │   │   ├── comment_service.py       # CommentService (any member posts, author/owner deletes)
│   │   │   └── vote_service.py          # VoteService (advisory voting on AI-proposed changes)
│   │   ├── subscriptions/               # ── IMPLEMENTED (Phase 6) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # SubscriptionService (plan CRUD, subscribe/cancel — no payment collection)
│   │   ├── trials/                      # ── IMPLEMENTED (Phase 6) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # TrialService (global config + per-user one-time trial grants)
│   │   ├── entitlements/                # ── IMPLEMENTED (Phase 6) ──
│   │   │   ├── schemas.py
│   │   │   ├── rules.py                 # pure decision: kill switch > override > open-to-all > plan
│   │   │   └── service.py               # EntitlementService (cached global toggles, decision, require)
│   │   ├── admin/                       # ── IMPLEMENTED (Phase 7) ──
│   │   │   ├── schemas.py
│   │   │   ├── admin_service.py         # AdminService (DB-driven RBAC, roster + role mgmt, denial auditing)
│   │   │   ├── rbac_sync.py             # sync the catalog / system roles into the database
│   │   │   ├── user_management_service.py  # UserManagementService (search/block/delete/revoke)
│   │   │   ├── messaging_service.py     # AdminMessagingService (direct in-app/email messages)
│   │   │   └── broadcast_service.py     # BroadcastService (creates job, enqueues Celery task)
│   │   ├── security/                    # ── IMPLEMENTED (Phase 8) ──
│   │   │   └── service.py               # SecurityService (IP blocking, login-attempt log, events)
│   │   ├── notifications/               # ── IMPLEMENTED (Phase 8) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # NotificationService (notify() entry point + preferences)
│   │   ├── analytics/                   # ── IMPLEMENTED (Phase 8, partial) ──
│   │   │   └── service.py               # AnalyticsService (AI usage/cost recording)
│   │   ├── attachments/                 # ── IMPLEMENTED (Phase 8) ──
│   │   │   ├── schemas.py
│   │   │   ├── service.py               # AttachmentService (upload -> extract -> confirm -> link)
│   │   │   └── structured_extraction.py # regex date/amount/confirmation-code detection
│   │   ├── travel_history/              # ── IMPLEMENTED (Phase 8) ──
│   │   │   ├── schemas.py
│   │   │   └── service.py               # TravelHistoryService (mark_trip_completed, last-trip-to)
│   │   ├── discover/                    # ── IMPLEMENTED (Phase 8) ──
│   │   │   ├── schemas.py
│   │   │   ├── scoring.py               # pure, independently-tested scoring/cost-breakdown logic
│   │   │   └── service.py               # DiscoverService (the full section 10-12 pipeline)
│   │   │
│   │   │   # ── SCAFFOLDED (roadmap docstring only, see table below) ──
│   │   ├── profiles/        ├── preferences/     ├── payments/
│   │   ├── audit/           ├── ai/              └── cache/
│   │   (`conversations/` messaging plumbing is implemented inside
│   │    `companion/` above — the standalone module stays scaffolded)
│   │
│   ├── providers/                       # provider-abstraction layer (never couple to one vendor)
│   │   ├── geolocation/ipinfo_provider.py   # ── IMPLEMENTED ── IPinfo as a real provider, Redis-cached
│   │   ├── storage/                     # ── IMPLEMENTED ──
│   │   │   ├── interface.py             # StorageProvider ABC, UploadResult
│   │   │   ├── cloudinary_provider.py   # avatars + destination images
│   │   │   ├── supabase_storage_provider.py  # attachments, trip PDFs, travel docs
│   │   │   └── factory.py               # routes UploadUseCase -> correct provider
│   │   ├── email/                       # ── IMPLEMENTED ──
│   │   │   ├── interface.py
│   │   │   ├── brevo_provider.py        # OTP + transactional email via Brevo HTTP API
│   │   │   ├── queued_provider.py       # QueuedEmailProvider: hands every email to a Celery worker
│   │   │   ├── factory.py               # get_email_provider() — services receive providers by injection
│   │   │   └── mock_provider.py         # Phase 8: MockEmailProvider (in-memory sent-email log)
│   │   ├── payments/                    # ── IMPLEMENTED ── Paystack (initialize/verify/disable, signed webhooks) + mock
│   │   │   ├── interface.py
│   │   │   ├── paystack_parsing.py      # pure: HMAC-SHA512 signature check + payload normalization
│   │   │   ├── paystack_provider.py
│   │   │   ├── mock_provider.py
│   │   │   └── factory.py
│   │   ├── google/                      # ── IMPLEMENTED ── Google ID-token verification (JWKS) + mock
│   │   │   ├── interface.py
│   │   │   ├── google_provider.py
│   │   │   └── mock_provider.py
│   │   ├── malware/                     # ── IMPLEMENTED ── ClamAV (clamd) upload scanning + mock
│   │   │   ├── interface.py
│   │   │   ├── clamav_provider.py
│   │   │   ├── mock_provider.py
│   │   │   └── factory.py
│   │   ├── geocoding/                   # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── opencage_provider.py
│   │   ├── currency/                    # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── currencyapi_provider.py
│   │   ├── weather/                     # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── weatherapi_provider.py
│   │   ├── images/                      # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── unsplash_provider.py
│   │   ├── maps/                        # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── openrouteservice_provider.py
│   │   ├── amadeus/                     # ── IMPLEMENTED (Phase 2) ──
│   │   │   └── client.py                # shared OAuth2 client (token caching + request plumbing)
│   │   ├── hotels/                      # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── amadeus_provider.py
│   │   ├── flights/                     # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── amadeus_provider.py
│   │   ├── activities/                  # ── IMPLEMENTED (Phase 2) ──
│   │   │   ├── interface.py
│   │   │   └── amadeus_provider.py
│   │   ├── llm/                         # ── IMPLEMENTED (Phase 4) ──
│   │   │   ├── interface.py
│   │   │   ├── openai_provider.py       # primary/fallback model routing
│   │   │   └── mock_provider.py         # Phase 8: MockLLMProvider (scripted responses, no network)
│   │   ├── embeddings/                  # ── IMPLEMENTED (Phase 4) ──
│   │   │   ├── interface.py
│   │   │   └── openai_embeddings_provider.py  # not yet wired into a RAG pipeline
│   │   │
│   │   │   # ── SCAFFOLDED ──
│   │   └── speech_to_text/
│   │
│   ├── schemas/                         # cross-module shared Pydantic schemas (empty so far)
│   ├── services/                        # ── IMPLEMENTED ──
│   │   └── cache_service.py             # Redis-backed CacheService + deterministic key builders + TTLs
│   ├── repositories/                    # ── IMPLEMENTED ──
│   │   ├── user_repository.py
│   │   ├── otp_repository.py
│   │   ├── session_repository.py        # user_sessions (rotation / revocation)
│   │   ├── preferences_repository.py
│   │   ├── account_repository.py        # bulk export + deletion queries
│   │   ├── payment_repository.py        # payments, subscription events, webhook de-dup
│   │   ├── trip_preferences_repository.py
│   │   ├── trip_repository.py           # Phase 3: trips, members, days, items, versions
│   │   ├── place_repository.py          # Phase 2
│   │   ├── memory_repository.py         # Phase 4
│   │   ├── conversation_repository.py   # Phase 4
│   │   ├── knowledge_repository.py      # Phase 4 (pgvector cosine similarity search)
│   │   ├── pending_change_repository.py # Phase 4
│   │   ├── collaboration_repository.py  # Phase 5: invitations, comments, votes
│   │   ├── monetization_repository.py   # Phase 6: plans, subscriptions, trials, overrides
│   │   └── admin_repository.py          # Phase 7: admin users, audit log, messages, broadcasts
│   ├── utils/                           # ── IMPLEMENTED ──
│   │   ├── phone.py                     # international phone validation (phonenumbers)
│   │   ├── username.py                  # username validation/normalization rules
│   │   ├── otp_generator.py             # secure OTP + Wayva ID generation
│   │   ├── localization.py              # resolve_localization (Accept-Language + IP + phone-region -> country/currency/lang/tz)
│   │   ├── accept_language.py           # RFC 7231 Accept-Language parsing
│   │   ├── passwords.py                 # single password-rule source (signup/reset/change)
│   │   ├── net.py                       # spoof-proof client IP extraction (TRUSTED_PROXY_HOPS)
│   │   ├── files.py                     # filename sanitization + magic-byte content sniffing
│   │   ├── serialization.py             # JSON-safe serialization for data export
│   │   └── money.py                     # Decimal-safe minor-unit conversion (kobo/cents)
│   └── workers/
│       ├── celery_app.py                # Celery app (imports every *_tasks module)
│       ├── broadcast_tasks.py           # admin broadcasts
│       ├── planning_tasks.py            # itinerary generation
│       ├── email_tasks.py, email_delivery.py   # queued email delivery (retries; payload parked in Redis)
│       ├── attachment_tasks.py          # attachment extraction / OCR
│       ├── rag_tasks.py                 # knowledge-base ingestion (chunk + embed)
│       ├── maintenance_tasks.py         # Celery beat schedule + scheduled jobs
│       └── locks.py                     # Redis lock so scheduled runs never overlap
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_phone.py
│   │   ├── test_username.py
│   │   ├── test_cache_keys.py
│   │   ├── test_itinerary_validation.py
│   │   ├── test_companion_tools.py
│   │   ├── test_rag_chunking.py
│   │   ├── test_rag_retrieval_scoring.py
│   │   ├── test_collaboration_schemas.py
│   │   ├── test_monetization_constants.py
│   │   ├── test_admin_permissions.py, test_admin_rbac_rules.py, test_admin_service_rbac.py   # RBAC rules + service
│   │   ├── test_mock_providers.py
│   │   ├── test_ai_cost_estimation.py
│   │   ├── test_structured_extraction.py
│   │   ├── test_discover_scoring.py
│   │   ├── test_files_util.py, test_client_ip.py, test_password_rules.py     # hardening utilities
│   │   ├── test_error_utils.py, test_serialization.py, test_clamav_parser.py
│   │   ├── test_money.py, test_paystack_parsing.py, test_payments.py   # Paystack: checkout, verify, webhooks, renewals, cancellation
│   │   ├── test_planning_rules.py, test_planning_llm_io.py, test_planning_workflow.py   # generation rules, parsing, the graph end-to-end
│   │   ├── test_planning_service.py, test_snapshots.py     # jobs/idempotency/locking, persistence, version chain
│   │   ├── test_planning_revision.py                       # conversational revision: diff + workflow
│   │   ├── test_discover_logic.py, test_discover_graph.py, test_discover_service.py   # Discover rules, workflow, service
│   │   ├── test_entitlement_rules.py, test_entitlement_service.py, test_feature_gating.py   # access decision, service, router-scan guarantees
│   │   ├── test_companion_tool_gating.py                    # per-tool plan enforcement
│   │   ├── test_email_pipeline.py, test_attachment_processing.py, test_rag_ingestion_job.py   # worker pipelines
│   │   ├── test_lifecycle.py, test_trip_pdf.py              # scheduled jobs, PDF safety
│   │   ├── test_resilience.py, test_provider_resilience_integration.py   # retry/breaker/redaction + real providers
│   │   ├── test_rls_policies.py                             # RLS policy generation (every table, every escape hatch)
│   │   ├── test_durable_cache_and_usage.py                  # durable geocode/currency cache, usage repos, RLS setter
│   │   ├── test_saved_places.py                             # saved-places service + Companion tools
│   │   ├── test_localization.py                             # country dataset, Accept-Language, IPinfo, suspicious login
│   │   ├── test_hotel_replacement.py, test_flight_booking.py   # hotel swap + flight-add: pure logic + services
│   │   ├── test_companion_intents.py, test_companion_context.py, test_companion_graph.py   # routing, context, gating, allow-lists
│   │   └── test_companion_service.py, test_companion_registry.py   # graph-backed turns, revisions, confirmation, tool registry
│   │   └── test_security_regressions.py   # enum persistence, prod guards, OTP burn, uploads, paid-plan guard, refresh rotation, Google linking
│   ├── integration/                     # empty — DB/Redis/provider integration tests go here
│   └── api/                             # empty — endpoint-level tests go here
│
├── scripts/
│   ├── seed_dev_data.py                 # dev: super admin (with a real password), plans, sample places
│   ├── create_super_admin.py            # bootstrap the first Super Admin (any environment)
│   └── sync_rbac.py                     # sync the RBAC catalog into the database
└── docs/                                # empty — architecture notes go here
```

---

## Roadmap — what's scaffolded vs. implemented

> A ✅ below means the code path exists and compiles — **not** that it has been
> executed against a live PostgreSQL/Redis (see "What's still genuinely missing").
> Phase 6 and 7 rows describe the original delivery; the hardening pass fixed
> several holes in them (free premium access, unauthenticated-style overrides,
> revocation gaps) — see `CHANGELOG.md`.

| Phase (per Master Build Prompt) | Status |
|---|---|
| **Phase 1 — Foundation** (config, DB, logging, errors, security, auth, users) | ✅ Implemented, plus the 4 requested adjustments |
| **Phase 2 — Core Travel**: Geocoding (OpenCage), Currency (CurrencyAPI), Weather (WeatherAPI.com), Images (Unsplash), Maps/routing (OpenRouteService), Hotels/Flights/Activities (Amadeus) | ✅ Implemented (this delivery) — **Phase 2 complete** |
| Phase 2 — Core Travel: places (reference data + search) | ⏳ Scaffolded — small remaining Phase 2 item |
| **Phase 3 — Planning**: Trips, trip membership/authorization, structured itinerary (days/items), server-side validation, versioning/restore | ✅ Implemented (this delivery) — **manual/API-driven, not yet AI-generated** |
| Phase 3 — Planning: AI-driven itinerary generation, automatic repair-on-validation-failure, budget-violation checks, geographic-inefficiency scoring | ⏳ Depends on Phase 4 (AI/LangGraph) |
| **Phase 4 — AI**: LLM provider (OpenAI, primary/fallback routing), Embeddings provider (OpenAI), Memory (view/edit/delete/disable **+ automatic extraction**), RAG (pgvector ingestion + **hybrid reranked, metadata-filterable** retrieval), Companion (conversations/messages + **tool-calling loop**, RAG as a tool, **propose→confirm/reject itinerary changes**, **conversation summarization**) — every tool independently re-authorized | ✅ **Implemented — Phase 4 complete** (2 deliberate architectural exclusions, see below) |
| Phase 4 — AI: Intent routing as a separate classifier, LangGraph multi-step orchestration | ⚪ Deliberately not built — see the architectural-decision notes below for why |
| **Phase 5 — Collaboration**: Invitations (secure token, email-gated acceptance, 7-day expiry), member roles (owner/editor/contributor/viewer) with owner-only role changes/removal and last-owner protection, comments, advisory voting on AI-proposed changes | ✅ **Implemented — Phase 5 complete** |
| **Phase 6 — Monetization**: Plans, subscriptions (real state machine, no payment collection — see caveat below), trials (admin-configurable), EntitlementService (override → trial → subscription → free-tier resolution), feature flags gating a real endpoint | ✅ **Implemented — Phase 6 complete** |
| **Phase 7 — Admin**: RBAC (database-driven roles/permissions, see below), user management (search/block/unblock/soft-delete/session revocation), admin messaging, broadcasts (real Celery background job), append-only audit log, dashboard stats | ✅ **Implemented — Phase 7 complete** |
| **Phase 8 — Production hardening**: Rate limiting, failed-login lockout, security headers, mock providers, dev seed script, **+ admin-gating closure, security/notifications/analytics systems, trip notes/costs/routes, attachment OCR, voice input, trip PDF export, travel history** | ✅ **Implemented — see "What's still genuinely missing" for exact remaining scope** |

Every scaffolded package (`app/modules/<name>/__init__.py` and
`app/providers/<name>/__init__.py`) contains a docstring naming which
phase it belongs to and what it will contain — nothing fakes
functionality it doesn't have.

---

## What's implemented in detail

### Auth flow (OTP-based, no links)

```
POST /api/v1/auth/signup
  -> validates first/last name, unique username, international phone,
     email, password+confirm (one shared password rule set)
  -> derives wayva_id, country, currency, language, timezone
  -> creates User(is_active=False, status=pending)
  -> generates 6-digit OTP, emails it via Brevo (NOT a link)

POST /api/v1/auth/verify-email        { email, otp_code }
  -> verifies OTP -> is_active=True, status=active
  -> launch-mode trial starts automatically (if trials are enabled)
  -> creates a session and issues access + rotating refresh tokens

POST /api/v1/auth/resend-otp          { email }          (per-email cooldown)
POST /api/v1/auth/login               { email, password }   (blocked until verified)
POST /api/v1/auth/refresh             { refresh_token }  -> NEW access + NEW refresh token (rotation)
POST /api/v1/auth/logout              { refresh_token }  -> revokes this device's session
POST /api/v1/auth/logout-all                             -> revokes every device immediately
POST /api/v1/auth/change-password     { current_password, new_password, confirm_new_password }
POST /api/v1/auth/forgot-password     { email }          -> OTP via Brevo, no link
POST /api/v1/auth/reset-password      { email, otp_code, new_password, confirm_new_password }
GET  /api/v1/auth/username-available?username=...

POST /api/v1/auth/google              { id_token }
  -> verified server-side (signature, audience, issuer, expiry, verified email)
  -> "authenticated" + tokens   (returning user, or email matched an existing account)
  -> "profile_required" + signup_token  (brand-new Google identity)
POST /api/v1/auth/google/complete     { signup_token, username, phone_number, first_name?, last_name? }
  -> creates the account already active; Google profile picture saved automatically
```

OTP codes are stored **hashed** (bcrypt, same as passwords) — never in
plaintext — with expiry, single-use consumption, and attempt limiting.
The failed-attempt counter is committed *before* the error is raised, and
the code is burned once the limit is reached. `verify-email` and
`reset-password` are additionally rate-limited per IP and per account.

**Sessions.** Every login creates a `user_sessions` row. Refresh tokens
rotate on each use; presenting an already-rotated token after a short
grace window revokes that session and records a security event.
`sessions_invalidated_at` is the "sign out everywhere" switch and applies to
access *and* refresh tokens. Users can list and revoke devices at
`GET /users/me/sessions` and `DELETE /users/me/sessions/{id}`.

### Users (profile, preferences, onboarding, account lifecycle)

```
GET    /api/v1/users/me
PATCH  /api/v1/users/me                     first/last name, username, language, currency, timezone, country
                                            (any other field, e.g. email/role/is_active, is REJECTED)
DELETE /api/v1/users/me/avatar
GET    /api/v1/users/me/preferences
PATCH  /api/v1/users/me/preferences         partial update; optional & skippable
POST   /api/v1/users/me/onboarding/complete { skipped }
GET    /api/v1/users/me/sessions
DELETE /api/v1/users/me/sessions/{id}
GET    /api/v1/users/me/export              everything held about you, as JSON
POST   /api/v1/users/me/delete-account/request    -> OTP by email
POST   /api/v1/users/me/delete-account/confirm    { otp_code } -> anonymize + purge (irreversible)
```

Account deletion runs in one transaction: sessions revoked; owned trips
handed to a collaborator (editor > contributor > viewer) or deleted; private
data hard-deleted; subscription cancelled; emails in logs redacted; the
`users` row anonymized (status `deleted`) so audit/security history keeps
valid references. Stored files are purged right after commit (best effort).

### Discover (LangGraph)

```
POST /api/v1/discover/search   { budget_amount, budget_currency, duration_days, travelers, start_date?, end_date?,
                                 origin?, interests?, travel_style?, ..., max_results, exclude_visited }
```

```
prepare_constraints ─► cache_lookup ─(hit)──────────────────────────────────────► rank_results ─► persist
                             └(miss)─► normalize_currency ─► generate_candidates ─► enrich_candidates ─┘
                                            └── any failure ─► fail (honest error, nothing saved)
```

- **The budget is the TOTAL for the whole group** ("I have ₦700,000, where can I go for 7 days?"). The
  model is told the per-person-per-day figure; affordability is judged against the total. (Before, the prompt
  said "per traveler" while scoring compared the group total — the two disagreed.)
- **One real exchange rate.** USD→budget currency is fetched once; if it is unavailable the search fails
  with a retryable error. The old fallback treated a USD figure as if it were in the budget currency, which
  made naira budgets look absurdly affordable.
- **Verification (systems verify).** Each candidate is geocoded (concurrently, bounded), its country must
  agree with the model's claim, and it must have a realistic positive cost — a missing/zero estimate used to
  score as "free" and win the budget ranking. Anything that fails is dropped, never shown with invented data.
- **Dates matter.** The trip's start date is part of the cache key; a season-fit score (is the trip in the
  destination's best months?) enters the ranking when dates are given; the weather line only describes
  forecast days inside the traveller's dates and only within the forecast horizon (it used to describe "the
  next N days", which is the wrong week for a trip months away).
- **Personalization, reported.** The request always wins; the user's saved preferences fill only the gaps and
  are named in `notes` ("Used your saved interests."). Places from the user's travel history are left out
  unless `exclude_visited=false` — applied AFTER the shared cache, so one user's history never affects another's.
- **Honest costs.** Every breakdown is `source="estimated"` and `notes` states that flights are excluded.
  `trip_planning_cta.budget_amount` is now the user's own budget (it used to be the estimate).

### Companion (LangGraph + intent routing)

A message runs as a graph (`app/modules/companion/graph.py`):

```
classify_intent ─► check_access ─┬─► load_context ─► agent ─► reply
                                 └─► deny (polite refusal — no model, no tools, no data)
```

- **Intent routing (§83).** 12 intents (`GENERAL_TRAVEL, TRIP_QUERY, ITINERARY_EDIT, HOTEL_SEARCH,
  FLIGHT_SEARCH, ACTIVITY_SEARCH, WEATHER_QUERY, CURRENCY_QUERY, TRAVEL_HISTORY, DISCOVERY,
  ACCOUNT_QUERY, UNKNOWN`). Deterministic rules classify the obvious requests for free ("Remove
  Day 3", "Convert 500 USD to NGN"); only ambiguous messages cost a one-word call to the **fast**
  model tier. If classification fails the request degrades to a read-only tool set.
- **Feature gating per intent.** `HOTEL_SEARCH`→`HOTELS`, `FLIGHT_SEARCH`→`FLIGHTS`,
  `ACTIVITY_SEARCH`→`ACTIVITIES`, `WEATHER_QUERY`→`WEATHER`, `ITINERARY_EDIT`→`PLANNER`,
  `DISCOVERY`→`DISCOVER` (on top of `COMPANION` for the endpoint), evaluated server-side each turn.
- **Relevant context only (§36).** The conversation summary, the conversation's trip (membership
  re-verified right now; item ids included so edits need no extra lookup), stored preferences and
  memories are loaded only for the intents that can use them — a currency question loads none.
- **Per-intent tool allow-lists.** The agent may only use its intent's tools; a tool the model asks
  for outside the list is refused (and never executed). Only `ITINERARY_EDIT` is offered any
  write-capable tool. Every tool also re-authorizes the user itself (§40).
- **Tools (20).** Adds `propose_itinerary_revision`, `create_trip_version` (append-only checkpoint),
  `calculate_route`, `search_destinations`. `get_saved_places` is not built (there is no saved-places
  feature yet).
- **Cost-aware model routing (§97).** `tier="fast"` for classification, summaries and memory
  extraction; `tier="strong"` (with a long timeout) for planning; `AI_FAST_MODEL` / `AI_STRONG_MODEL`
  override the primary model per tier. The LLM provider is injected — no module-level client.

**Conversational itinerary revision (§18).** "Make this trip cheaper", "remove Day 3", "reduce
walking", "replace the hotel" → `propose_itinerary_revision` runs a second LangGraph workflow
(draft → ground → validate → repair) that reuses the generation nodes, so a revision is held to the
same rules as a fresh plan. Verified provider items (hotels/activities) keep their stored,
provider-verified name/price/coordinates — the model cannot edit them. Nothing is applied: the
result is a **pending proposal** with a diff summary ("removes 2 items…, estimated cost 1,210 → 980
EUR") holding the whole validated plan and the trip's version at proposal time. Confirming it
(owner/editor) applies it in one transaction with a new version snapshot — and is **refused if the
trip changed in the meantime**, so a stale proposal cannot overwrite newer edits. A revision keeps
the trip's dates; "remove Day 3" leaves that day free.

### Itinerary generation (LangGraph)

```
POST /api/v1/trips/{trip_id}/generate          { city_code?, include_hotels?, include_activities?, preferences? }
                                               header: Idempotency-Key (recommended)
                                               -> 202 + job (production)  |  200 + finished job (development)
GET  /api/v1/trips/{trip_id}/generate/{job_id} -> { status: queued|running|succeeded|failed, result | error }
GET/PUT /api/v1/trips/{trip_id}/preferences    planner preferences (pace, walking, interests, food, must-see, avoid, ...)
```

Everything about the trip (dates, destination, travellers, budget) comes from the stored
trip; the request can only add preferences. Gated by the `PLANNER` feature flag, owner/editor
only (re-checked when the job actually runs), rate limited, one generation per trip at a time.

The workflow (`app/modules/planning/graph.py`, executed by LangGraph):

```
geocode_destination → gather_data → normalize_currency → draft_itinerary → ground_items → validate ─┬─► persist
        │                (hotels ∥ activities ∥ weather)                         ▲                  ├─► repair ─┘ (≤ 2 rounds)
        └─► fail                                                                 └───────────────────┴─► fail
```

- **AI proposes, systems verify.** The destination must geocode. Hotels/activities come from
  Amadeus, the forecast from the weather provider, prices are converted with real exchange
  rates. A model-referenced hotel/activity keeps the *provider's* name, price and coordinates
  (the model cannot set them); its own suggestions are marked `estimated` and their locations
  are verified by geocoding — coordinates the model supplies are never trusted.
- **No fabrication.** If a provider is down or unconfigured the job says so in `data_sources`
  (`ok | unavailable | skipped`) and plans without it; an unavailable exchange rate leaves a
  price unknown rather than guessed. No `city_code` → hotels are skipped (there is no
  IATA lookup yet). Beyond the ~10-day forecast horizon no weather claims are made.
- **Validation before saving (§16).** Wrong day count/dates, empty days, overlapping items,
  end-before-start, invalid or far-off coordinates, impossible travel time between consecutive
  stops, duplicates, budget overruns and currency mismatches are errors. Repeated items, a
  missing hotel, too-busy days and outdoor plans on wet forecast days are warnings.
- **Repair.** Deterministic fixes first (drop duplicates, re-time clashes with travel time,
  clear unverifiable coordinates, trim the most expensive *optional* paid items — never a hotel,
  a must-see, or a day's last item). Then up to two model revisions for what remains. A weather
  conflict gets one revision attempt and is never looped on.
- **A plan that still has an error is never saved.** The job fails with a retryable error.
- **Persistence** is one transaction: days/items replaced, overview + status set, and a new
  **version** snapshot (parent-linked and fully restorable, including provider, description, notes).
  It refuses to write if the trip's dates changed while the AI was working.
- **Long AI workflows run in the worker** (`planning.generate` Celery task); development runs
  inline. `PLANNING_EXECUTION=inline|background` overrides the default.

### Payments (Paystack)

```
POST /api/v1/payments/checkout         { plan_id }   -> { authorization_url, reference, access_code, public_key }
POST /api/v1/payments/verify           { reference } -> payment (call when the customer returns from Paystack)
GET  /api/v1/payments/me?limit&offset                -> your payment history
POST /api/v1/payments/webhook/paystack               <- Paystack (no JWT; HMAC-SHA512 signature + source-IP check)
POST /api/v1/subscriptions/cancel                    -> stop renewal, keep access to period end
PATCH /api/v1/plans/{id}               (plans:manage) link a Paystack plan code / (de)activate
GET  /api/v1/admin/payments?status&limit&offset      (payments:view) finance view
POST /api/v1/admin/payments/{id}/refund { amount_minor?, reason }  (payments:refund) ask Paystack to refund
```

Setup: create the plan in the Paystack dashboard (recurring plans need a
Paystack plan whose amount, currency and interval match ours), then call
`PATCH /plans/{id}` with its `paystack_plan_code`; set the webhook URL
`https://<api-host>/api/v1/payments/webhook/paystack`; put the keys in `.env`.
A plan without a Paystack code is a one-off purchase that simply ends at the
period end.

How it stays safe:
- The client sends only `plan_id`. **Amount and currency come from our plan
  record**; the amount is converted to minor units with `Decimal` (no float
  drift) and sent to Paystack with a unique `TW-<uuid>` reference.
- A payment is applied only after Paystack **confirms** it (server-to-server
  verify, or a signed webhook) **and** the confirmed amount + currency equal
  what we asked for. A mismatch marks the payment failed, activates nothing,
  and raises a critical security event.
- Webhooks: signature checked over the raw body before anything is parsed;
  each delivery is de-duplicated by SHA-256 of its body (atomic
  `INSERT ... ON CONFLICT DO NOTHING`), so redelivery is a no-op.
- The verify path and the webhook can race: the payment row is locked
  (`SELECT ... FOR UPDATE`) and the payment, subscription and events commit in
  one transaction, so a charge activates at most once.
- Renewals (`charge.success` for a reference we did not create) are matched to
  the customer's subscription by Paystack customer + plan code and extend the
  period; a wrong renewal amount is rejected. `subscription.create` is handled
  in either arrival order; `subscription.disable` / `not_renew` stop
  auto-renew; `invoice.payment_failed` notifies the user.
- Cancelling a paid, auto-renewing subscription disables it **at Paystack
  first**; if that call fails the subscription is NOT marked cancelled locally
  (otherwise the customer would keep being charged). Access continues until the
  paid period ends. Buying another plan disables the previous auto-renewing one.
- **Webhook source IP**: on top of the signature, requests are checked against
  `PAYSTACK_WEBHOOK_IP_ALLOWLIST` (Paystack's three published IPs by default). An unlisted IP with a
  valid signature is still processed but logged and recorded as a `paystack_webhook_unlisted_ip`
  security event; set `PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST=true` to reject it with 403 instead
  (only once `TRUSTED_PROXY_HOPS` is right for your deployment, or every webhook will appear to
  come from your proxy). The signature check runs first, so scanners never generate events.
- **Refunds** (`POST /admin/payments/{id}/refund`, `payments:refund` — finance admin and super admin
  only, since it moves money out): Paystack refunds are asynchronous, so the request only asks
  Paystack and returns its `pending` status; nothing changes locally until the signed
  `refund.processed` webhook arrives. The amount is capped at what is still refundable, the call is
  never auto-retried (a timed-out request may have been accepted, and a retry could refund twice),
  and every attempt is audit-logged. A processed **partial** refund records `refunded_amount_minor`
  and keeps access; a **full** refund marks the payment `refunded`, ends the subscription it was
  funding, and disables it at Paystack so the customer stops being billed. It only cuts access if it
  is the payment funding the *current* period — refunding an old month never revokes time paid for
  since. Refunds made directly in the Paystack dashboard are synced the same way. `refund.failed`
  is recorded and changes nothing; an unmatched refund is parked for manual reconciliation rather
  than guessed.
- **Chargebacks**: `charge.dispute.create` marks the payment `disputed`, revokes access at once
  (fail toward not granting it), and raises a critical security event. `charge.dispute.resolve` is
  recorded but never restores access automatically — a person decides.
- Revenue analytics report **net** of refunds (fully refunded/disputed payments drop out,
  partial refunds count only what was kept); the daily metrics gain
  `payments_refunded_amount_minor:<CCY>`.
- A subscription is current only while `status=active` **and**
  `current_period_end` is in the future — previously nothing ever expired one.

### Provider resilience (Master Prompt §78)

Every outbound HTTP-backed provider (weather, currency, geocoding, images, maps, Amadeus
hotels/flights/activities, OpenAI chat/embeddings/Whisper, IPinfo, Paystack) now goes through
the same three layers instead of a single try/except around `httpx`:

1. **Bounded retry with backoff.** Transient failures (timeouts, connection errors, HTTP 429/5xx)
   are retried up to `PROVIDER_RETRY_ATTEMPTS` (default 3) with exponential backoff + jitter,
   honouring a provider's `Retry-After` header (capped so a hostile/buggy header cannot make us
   wait forever). **Permanent failures (4xx other than 429) are never retried** — a bad API key
   or bad request answering the same way five times wastes latency without helping, and a 404
   from Amadeus is correctly treated as "not found", not as the provider failing.
2. **A circuit breaker per provider.** After `CIRCUIT_FAILURE_THRESHOLD` (default 5) consecutive
   transient failures the circuit OPENS: further calls fail in microseconds (`CircuitOpenError`,
   itself a `ProviderUnavailableError` — existing callers need no changes) for
   `CIRCUIT_RECOVERY_SECONDS` (default 30) instead of each one waiting out a timeout. Then ONE
   probe call is allowed through; success closes the circuit, failure re-opens it. State is
   per-process (so it protects that process's event loop immediately) and each result is also
   published to Redis (`GET /admin/provider-health`, `analytics:view`) for visibility across
   processes. Amadeus gets a **separate breaker per resource** (`amadeus_hotels`, `_flights`,
   `_activities`) — flight search being down must not stop hotel search from being tried.
3. **No secrets in logs.** `redact_secrets` strips API-key/token-looking query parameters and
   `Authorization` headers before any provider error is logged — several providers (WeatherAPI,
   OpenCage, CurrencyAPI) pass their key as a **query parameter**, and `httpx`'s own exception
   messages embed the full request URL, so logging `str(exc)` directly had been writing live API
   keys into the logs.

Retrying is only used where it is safe: a Companion turn that already asked the model to call
tools is retried at most once (a second call could choose different tool arguments), and Paystack
initiate/verify get their own breaker on top of Paystack's existing idempotency-safe retry.
IPinfo (best-effort signup enrichment) and Whisper get the breaker but no extra retry — one
attempt only, so they never add meaningful latency to a request that is not about them.

### Missing tables closed (usage tracking, durable caches, RLS)

- **`provider_usage`** — one row per EXTERNAL provider call, written automatically by the
  resilience layer's observer (`app/core/resilience.py`) for every provider wired through it —
  nothing to instrument per call site. **`api_usage`** — one row per INBOUND request (method,
  route template — not the raw path, so a UUID in the URL does not explode cardinality — status,
  duration), written by middleware, sampled via `API_USAGE_SAMPLE_RATE` (default 1.0). Both get
  their own retention windows (`PROVIDER_USAGE_RETENTION_DAYS`, `API_USAGE_RETENTION_DAYS`,
  default 30 days).
- **`geocoding_cache` / `currency_cache`** — durable copies behind Redis for the two lookups
  worth surviving a Redis restart/eviction: `Redis (fast) → durable Postgres cache → live
  provider`. Each row carries its own `expires_at`, judged the same way whether the hit came
  from Redis or here; a durable hit is promoted back into Redis so the next request is fast
  again. `currency_cache` keeps history (useful for reviewing past rates); old, expired rows are
  pruned by retention (`CURRENCY_CACHE_RETENTION_DAYS`) — a row still within its own `expires_at`
  is the current rate and is kept regardless of age.
- **Row-Level Security** (migration `0021`) — a second, database-enforced layer of authorization
  underneath the application's own checks, covering trips (owner or active collaborator),
  everything that hangs off a trip, and the other directly user-owned tables (memories,
  conversations, messages, notifications, sessions, preferences, attachments, discovery
  searches/results). **Non-breaking by default**: every policy also passes when the session
  variable it checks is unset, so nothing changes until an operator sets `RLS_ENFORCE=true`
  *and* the app connects as a non-superuser Postgres role. When enforced, `get_current_user`
  sets `app.user_id` for the request's transaction (`SET LOCAL`, auto-reset after) — the single
  hook point already running on every authenticated request.

### Background work (Celery workers + beat)

Nothing slow or flaky runs inside an API request (Master Prompt §69). `TASKS_EXECUTION=background|inline`
(default: background in staging/production, inline in development) controls the first three; itinerary
generation has its own `PLANNING_EXECUTION`. Run one or more `worker` processes and exactly ONE `beat`
(`docker-compose.yml` has both).

| Work | Where | Notes |
|---|---|---|
| **Emails** (OTPs, invitations, admin messages, notifications) | `QueuedEmailProvider` → `email.deliver` | Every caller becomes asynchronous with no code change (the factory returns the queued provider). The message is parked in Redis under a random one-time key and the task carries **only that key**, so an OTP code never appears in broker task arguments or Celery's failure logs. Retries with backoff for transient errors (5xx/429/network); a permanent 4xx rejection is not retried. If the broker is down the email is sent directly rather than lost. |
| **Attachment extraction / OCR** | `attachments.process` | Upload returns immediately with `extraction_status: "pending"`; poll `GET /attachments/{id}`. Row-locked and idempotent; a corrupt file → `failed` (no retry), a storage outage → retried, then `failed`. |
| **Knowledge-base ingestion** | `rag.ingest` | `POST /rag/documents` → 202 `processing`; poll `GET /rag/documents/{id}` until `ready`. Embeds in batches; chunks and status commit together. |
| **Itinerary generation** | `planning.generate` | see "Itinerary generation". |
| **Broadcasts** | `broadcast.send` | as before. |

**Scheduled jobs** (`app/workers/maintenance_tasks.py`; each takes a Redis lock, does idempotent work, rows are fetched
`FOR UPDATE SKIP LOCKED`):

| Job | When | What |
|---|---|---|
| `payments.reconcile` | every 10 min | Verifies PENDING payments whose webhook/return never arrived (a customer who paid but whose browser and webhook both failed would otherwise pay and get nothing). Same safety checks as the normal path; unpaid ones are abandoned (provider says so, or after 48 h). |
| `maintenance.lifecycle` | hourly | ONE trial-ending reminder, ONE trial-ended notice, ONE "plan ending" reminder for non-renewing subscriptions, and the expiry sweep (status → `expired` + notice). Auto-renewing subscriptions get `SUBSCRIPTION_GRACE_HOURS` (24) of grace — in the sweep AND in access itself — so a renewal webhook that is a little late never locks a paying customer out. |
| `maintenance.retention` | daily | Deletes finished OTPs (48 h), dead sessions (30 d), login attempts incl. IPs (90 d), email logs (90 d), read notifications (180 d), old webhook de-dup rows (90 d). Never touches the admin audit log, payments or security events. |
| `analytics.daily_metrics` | daily | Aggregates the last two complete days into `daily_metrics` (registrations, activations, trips, generated itineraries, Companion turns, AI requests/tokens/cost, payments per currency, failed logins); `GET /admin/analytics/daily?days=30` (`analytics:view`). |
| `cache.warm_currency` | every 6 h | Refreshes the common `CURRENCY_WARM_PAIRS`. |

Security fixes made while moving this work: OTP emails were logged with their **code in the subject** in `email_logs`
(now redacted); user-controlled names and trip titles were inserted **unescaped into email HTML** — including
invitation emails sent to third parties (now escaped); PDF export interpreted user text as reportlab markup (an
unbalanced `<b>` in a title crashed the export, and tags like `<img src=…>` were interpreted) and used the raw trip
title as a storage path (now escaped/sanitized, and rendered off the event loop).

### Saved places

A user's personal bookmark list, independent of any trip — free tier, like Discover itself.

```
POST   /api/v1/saved-places            { name, category, latitude, longitude, city?, country?, notes?, trip_id? }
GET    /api/v1/saved-places?trip_id=   list (optionally filtered to one trip)
DELETE /api/v1/saved-places/{id}
```

Denormalized rather than a foreign key to `places`: most of what a user saves comes from a live
provider result (a Discover destination, a hotel, an activity) that was never ingested into the
shared reference table, so saving never requires that extra step. Idempotent by (user, name,
~100m) — saving the same place twice returns the existing bookmark rather than duplicating it,
which matters for the Companion's `save_place` tool since the model may plausibly call it twice
for one request. `get_saved_places` / `save_place` sit in the Companion's read-only/general tool
sets across most intents (not gated — bookmarking needs no plan).

### Localization

- **196-country dataset** (`app/core/country_data.py`): currency, primary language, primary
  timezone per ISO 3166-1 alpha-2 code, replacing the previous ~20-country starter map.
- **`Accept-Language` is now actually used** — previously documented in the signal-priority list
  but never wired up. `app/utils/accept_language.py` parses it (RFC 7231): a region subtag (e.g.
  `en-US` → `US`) is the highest-priority COUNTRY signal, ahead of IP; the language subtag is
  used for LANGUAGE specifically even when a different signal wins country (a browser saying
  `es` is a better language signal than a multilingual country's single default language).
- **IPinfo is now a real provider** (`app/providers/geolocation/ipinfo_provider.py`): resilient
  (retry/circuit-breaker) via the shared HTTP layer, Redis-cached by IP for
  `IPINFO_CACHE_TTL_SECONDS` (24h) — the same IP recurs across retried signups and, now, every
  login (see below), and country-from-IP is stable enough over a day to cache.
- **Suspicious-login analysis** (`app/modules/security/suspicious_login.py`): on every successful
  login except the very first, the login IP's country is compared to the account's own country;
  a mismatch records a `SecurityEvent` and sends a `SECURITY_ALERT` notification —
  **informational, never blocking**, since travel is the whole point of this product. Toggle with
  `SUSPICIOUS_LOGIN_DETECTION`. A missing signal on either side never fabricates a flag.

### Itinerary depth: hotel replacement & flights

**Hotel replacement** (`PUT /trips/items/{item_id}/replace-hotel`, Companion `replace_hotel`) —
swaps a trip's hotel for a specific hotel found via search, deterministically: fetches a FRESH
live offer for that exact hotel (Amadeus now supports fetching by `hotel_id` directly, added
alongside this), converts its price into the trip's own currency with a real exchange rate (never
guessed — an unavailable rate leaves the cost honestly unknown rather than fabricated), and
writes it with a new itinerary version. No AI call: picking a search result is a well-defined,
deterministic operation, unlike an open-ended request ("find something cheaper"), which still
goes through `propose_itinerary_revision`.

**Flights** (`POST /trips/{trip_id}/flights`, Companion `add_flight_to_trip`) — adds a specific,
already-searched flight offer to a trip day. Unlike hotels, Amadeus flight offers are ephemeral
and cannot be re-fetched by id — but `search_flights` now caches each raw offer server-side
(10 min TTL, keyed by offer id) precisely so it CAN be resubmitted to Amadeus's Flight Offers
Price API for a live re-confirmation at add-time. When that reprice succeeds, the item is saved
with the provider-confirmed price/currency and `source=provider`; when it can't be confirmed
(cache expired, Amadeus unreachable), the item is still added — using the just-searched
snapshot — but honestly marked `source=client_snapshot`, with `price_verified=false` and a
`verification_note` in the response, rather than silently claiming a live guarantee that was
never actually checked. Structural validation (arrival after departure, positive price, valid
IATA codes, departure date matches the trip day) still happens first, before any reprice attempt.

**IATA city-code auto-resolution** (Amadeus `resolve_city_code`, cached 30 days — a destination's
code never changes) — hotel search in itinerary GENERATION previously required the client to
supply a 3-letter city code, and silently skipped hotels entirely if it was missing; the
destination name is now resolved automatically when no code is supplied.

**Opening-hours verification** (Master Prompt §16, §26) — after a plan is built, activities,
attractions and timed restaurants with coordinates are checked against OpenStreetMap's
`opening_hours` tag, fetched via the keyless Overpass API
(`app/providers/opening_hours/overpass_provider.py`, behind an `OpeningHoursProvider` interface
so a different source can replace it later). This is a new provider outside the Master Prompt's
original list — Amadeus Tours & Activities has no structured hours, so implementing this at all
meant going outside it. Because it is community-maintained, third-party data:
- the OSM element is matched to the itinerary item by NAME (`app/providers/opening_hours/matching.py`)
  as well as proximity (a 75 m Overpass radius by default) — coordinates alone are not reliable
  enough in a dense city, and a wrong match would put another business's hours on the plan; a
  missed match just means the item is left unchecked, never a false alarm
- a conflict is always a WARNING (`opening_hours_conflict`), never a hard validation error, and
  the message always names OpenStreetMap as the source and says it can be out of date
- the model gets exactly one repair attempt for it (same as a weather conflict), then it is left
  as a warning
- the `opening_hours` string itself is parsed with an intentionally narrow, well-tested subset of
  the OSM syntax (`app/utils/opening_hours.py`) — day ranges, multiple time ranges, midnight
  crossing, `off`, `;`-separated overrides, `PH`/`SH` recognised-and-ignored — anything outside
  that (months, week numbers, sunrise/sunset, free-text comments, open-ended times, non-OSM day
  abbreviations like "Mon") is refused rather than guessed, so "I couldn't parse this" can never
  turn into "the venue must be closed"
- lookups are capped, run concurrently, time-boxed for the whole batch
  (`OPENING_HOURS_MAX_LOOKUPS_PER_PLAN`, `OPENING_HOURS_TOTAL_TIMEOUT_SECONDS`), and results are
  cached 7 days (misses 1 day) — a slow or unreachable Overpass degrades to no data, never a
  failed itinerary generation
- off by default is not the behaviour: `OPENING_HOURS_PROVIDER=overpass` is the shipped default;
  set it to `none` to disable the check entirely (planning behaves exactly as before)
- itinerary REVISIONS (Companion edits) opt into the same check, so a requested change cannot
  quietly move a visit to a time the venue looks closed without at least a warning

**Hotel image enrichment** — the top 3 hotel candidates in a generated itinerary now get a
thumbnail (Unsplash, the same enrichment Discover already had), bounded to limit extra image-
search calls; a failed lookup never blocks planning — the item is simply shown without one.

### Uploads (provider routing)

| Endpoint | Use case | Provider |
|---|---|---|
| `POST /api/v1/uploads/avatar` | User avatar | **Cloudinary** (optimized, CDN, deterministic `public_id` per user so re-upload replaces) |
| `POST /api/v1/attachments/upload?category=attachment\|travel_document` | **The** upload path for user files (returns the full record) | **Supabase Storage** (the category's private bucket, signed URL) |
| `POST /api/v1/uploads/trip-pdf/{trip_id}` | A PDF for a trip | **Supabase Storage** (trip-PDF bucket) |
| `POST /api/v1/uploads/attachment` *(deprecated)* | Wrapper over the tracked path, category `attachment` | **Supabase Storage** |
| `POST /api/v1/uploads/travel-document` *(deprecated)* | Wrapper over the tracked path, category `travel_document` | **Supabase Storage** |
| *(internal, no HTTP endpoint yet)* `UploadService.upload_destination_image` | Destination imagery | **Cloudinary** |

**Every Supabase-stored upload gets an `attachments` row** (`category` = `attachment` |
`travel_document` | `trip_pdf`), whichever endpoint it came through — the raw endpoints are thin
wrappers over `AttachmentService` — so nothing is stored unlisted. The category also routes the row
back to its own bucket when a fresh signed URL is minted (server-generated itinerary PDFs, which
are `trip_pdf` rows, used to be signed against the attachments bucket). `attachment` files are
text-extracted; `travel_document` files (passports, visas, IDs) are stored privately and
deliberately **not** OCR'd, so their text never lands in the database. `trip_pdf` cannot be
uploaded through the generic path.

Every upload is validated server-side: bounded streaming read (size limit),
the REAL type from magic bytes (the client `Content-Type` is ignored; macro-enabled
Word files are refused), optional ClamAV scan (`CLAMAV_HOST`; `REQUIRE_MALWARE_SCAN=true`
fails closed), sanitized filename, random storage-key prefix, no overwrite. Trip PDFs
require owner/editor access to the trip. Avatar uploads are saved on the user record.
Attachment URLs are minted fresh (short-lived signed URLs) on every read.

The routing table lives in one place —
`app/providers/storage/factory.py` — so it can be changed without
touching any router or service code.

### Phase 2 — Core Travel (Geocoding, Currency, Weather)

All three follow the same "reuse before regenerate" pattern
(`app/services/cache_service.py`): check Redis with a deterministic
key first, only call the live provider on a miss, and cache the
result with a TTL sized to how fast that data goes stale.

```
POST /api/v1/location/geocode          { query }               -> OpenCage (single best match)
POST /api/v1/location/suggest          { query (2+), limit }   -> OpenCage autocomplete: up to 6 cities/regions/countries worldwide, cached; [] when nothing matches
POST /api/v1/location/reverse-geocode  { latitude, longitude }  -> OpenCage
  cache TTL: 30 days (stable geographic data)

GET  /api/v1/currency/rates?base=USD&target=NGN                -> CurrencyAPI
POST /api/v1/currency/convert          { amount, base, target } -> CurrencyAPI
  cache TTL: 1 hour; base == target short-circuits to rate 1.0 (no provider call)

GET  /api/v1/location/approximate                             -> IPinfo (+ OpenCage fallback)
  the caller's APPROXIMATE location from their IP address (city, region, country,
  latitude, longitude, timezone), falling back to the country the account was
  localized to; `source` is "ip" or "account_country"; 422 `location_unavailable` if neither
  resolves. Cache: IP -> location 24 h. The raw IP is never returned.

GET  /api/v1/weather/current[?latitude=&longitude=]            -> WeatherAPI.com
  coordinates optional: with both -> that point (location_source "explicit"); with neither
  -> the approximate location above (location_source "ip" | "account_country", plus
  city/region/country for the widget label). Only one of the two -> 422.
GET  /api/v1/weather/destination?latitude=&longitude=&days=5    -> WeatherAPI.com
  cache TTL: 15 min (current) / 3 hours (forecast)
```

Every response includes `source` (`"cache"` or `"live"`) and
`provider`, per Blueprint §99 (response metadata for user trust). If
the relevant API key isn't set in `.env`, these endpoints return
`503 provider_unavailable` rather than fabricating a rate, location,
or forecast (Blueprint §78, §108).

**New required env vars used here:** `OPENCAGE_API_KEY`,
`CURRENCY_API_KEY`, `WEATHER_API_KEY` (already present in
`.env.example`).

### Images (Unsplash) and Maps/routing (OpenRouteService)

```
GET  /api/v1/images/search?entity=Eiffel+Tower&locale=en          -> Unsplash
  cache key normalized as "image:eiffel_tower:en" (Blueprint §32); TTL 60 days
  every response includes photographer name + profile URL — display
  this attribution wherever the image is shown (Unsplash license requirement)

POST /api/v1/maps/route  { origin_lat, origin_lon, destination_lat,
                            destination_lon, mode }               -> OpenRouteService
  mode: "walking" | "driving" | "cycling" (transit not supported by
  the free ORS tier — requesting it returns 422, not a silent fallback)
  cache TTL: 7 days (road networks are stable)
```

**New required env vars used here:** `UNSPLASH_ACCESS_KEY`,
`ORS_API_KEY` (already present in `.env.example`).

### Hotels, Flights, Activities (Amadeus)

All three share one Amadeus API account and one OAuth2
client-credentials client (`app/providers/amadeus/client.py`), which
caches its access token in Redis (~30 min lifetime, refreshed a
minute early) so a search doesn't re-authenticate every call.

```
POST /api/v1/hotels/search
  { city_code, check_in, check_out, adults, max_hotels }
  -> Amadeus: hotel list by city, then live offers for those hotels
  cache TTL: 5 minutes (availability/prices move fast)

POST /api/v1/flights/search
  { origin, destination, departure_date, return_date?, adults, cabin? }
  -> Amadeus Flight Offers Search
  cache TTL: 3 minutes — the shortest of any cache in this codebase,
  because flight prices are the most volatile data type (Blueprint §61)

POST /api/v1/activities/search
  { latitude, longitude, radius_km }
  -> Amadeus Tours and Activities
  cache TTL: 6 hours (listings change far less than prices)
```

Every one of these **returns an empty `results: []` list on no
matches** — never a fabricated hotel, flight, or activity (Blueprint
§78, §108: "Do NOT invent flights... Do NOT return fake hotel
prices"). Set `AMADEUS_ENV=test` (default) while developing against
Amadeus's free self-service test environment, which has real but
limited data; switch to `production` only once your app is approved
for Amadeus's production tier.

**New required env vars used here:** `AMADEUS_CLIENT_ID`,
`AMADEUS_CLIENT_SECRET`, `AMADEUS_ENV` (already present in
`.env.example`).

### Phase 3 — Planning (Trips + structured itinerary)

**Important scope note:** the Master Blueprint's itinerary generation
(§14) is AI/LangGraph-driven, and Phase 4 (AI) isn't built yet. So in
this delivery, itinerary *items* are added directly through the API
(by a person today; by the future AI pipeline once Phase 4 lands) —
what's implemented is the full structured data model, authorization,
server-side validation, and versioning that generation will sit on
top of, not the AI generation step itself.

```
POST   /api/v1/trips                              create trip -> becomes owner
                                                    -> scaffolds one empty TripDay per calendar day
GET    /api/v1/trips[?bucket=all|drafts|upcoming|active|completed|archived]   list your trips (`all` hides the ones you archived)
GET    /api/v1/trips/{trip_id}                    get one trip (membership required)
POST   /api/v1/trips/{trip_id}/archive            archive the trip FOR YOU only (any member; idempotent)
POST   /api/v1/trips/{trip_id}/unarchive          bring it back into your lists
GET    /api/v1/trips/{trip_id}/itinerary          full structured itinerary: days + items, in order

POST   /api/v1/trips/days/{day_id}/items          add an item to a day (editor/owner required)
PATCH  /api/v1/trips/items/{item_id}              partial update (editor/owner required)
DELETE /api/v1/trips/items/{item_id}              remove an item (editor/owner required)

GET    /api/v1/trips/{trip_id}/versions           list version history (newest first)
POST   /api/v1/trips/{trip_id}/versions/{id}/restore
                                                    restore a prior snapshot; recorded as a NEW version
                                                    (history is append-only, never rewritten in place)
```

**Trip status (backend-authoritative).** `status` is the raw lifecycle column
(`draft | planned | ongoing | completed | cancelled`). Every trip response also carries what the UI
should show, computed for the VIEWING member:

| `display_status` | when | tab (`bucket`) |
|---|---|---|
| `generating` | an itinerary is being generated right now | `drafts` |
| `failed` | the first generation failed and the trip is still an empty draft | `drafts` |
| `draft` | created, no itinerary yet | `drafts` |
| `ready` | itinerary generated, trip starts more than `TRIP_UPCOMING_WINDOW_DAYS` (30) away | `upcoming` |
| `upcoming` | itinerary generated, trip starts within that window | `upcoming` |
| `active` | today (in the viewer's timezone) is between `start_date` and `end_date` | `active` |
| `completed` | recorded as completed, or the end date has passed | `completed` |
| `cancelled` | `status = cancelled` (reserved; nothing sets it yet) | `completed` |
| `archived` | the viewer archived it (per member) | `archived` |

Also returned: `is_archived`, `archived_at`, `updated_at`, and `generation`
(`{job_id, state: "generating" | "failed", error_code, error_message, retryable}`, present only while
generating or after a failed attempt — poll `GET /trips/{id}/generate/{job_id}` with that `job_id` to
resume after a reload). A failed RE-generation on an already planned trip keeps its normal badge but
still reports `generation.state = "failed"`. Job records live 24 h, after which an old failure is no
longer reported.

`/users/me` reports how the account signs in: `google_connected`, `has_password` and
`sign_in_methods` (`["password"]`, `["google"]` or both) in addition to `auth_provider`, because
linking Google to a password account flips `auth_provider` to `google` while the password still works.
A Google-only account (`has_password: false`) cannot use change-password; it sets one via forgot-password.


**Authorization** — every one of these re-verifies trip
membership/role server-side via `TripService.get_trip_authorized`,
never trusting the `trip_id`/`day_id`/`item_id` in the URL alone
(Principle 1). Read endpoints require any membership; writes require
`owner` or `editor` role. Only `owner` exists today (the creator) —
inviting `editor`/`contributor`/`viewer` collaborators is Phase 5.

**Validation** — every add/update runs
`ItineraryValidationService.validate_day` immediately afterward and
returns the result inline (`"validation": {"is_valid": ..., "issues": [...]}`)
rather than blocking the write. It currently catches: overlapping
scheduled times, duplicate activities (same title+location on a day),
out-of-range coordinates, and more than 8 timed items in one day.
Automatic *repair* of a failing itinerary (Blueprint §16) needs an AI
loop and is deferred to Phase 4; today the validation is advisory —
callers decide what to do with it.

**Versioning** — every write creates a new `TripVersion` row holding
a full JSON snapshot of that trip's days/items plus a human-readable
change summary. Nothing is ever overwritten in place, and restoring
an old version doesn't delete history — it applies the old snapshot
and then records *that* as a new version too.

### Places (closes out Phase 2)

```
POST /api/v1/places                 create a reference place (`content:manage` admin permission)
GET  /api/v1/places/search?query=&category=&latitude=&longitude=&radius_km=
```

Radius search uses a latitude/longitude bounding box (not true
haversine/PostGIS distance) — accurate enough at city scale, and a
documented candidate for a Phase 8 precision upgrade.

### Phase 4 — AI (partial): LLM, Embeddings, Memory, Companion

**Read this before assuming Companion is the full blueprint spec.**
What's built:

```
POST   /api/v1/memory                          create a memory (source=user_stated)
GET    /api/v1/memory                           list your own memories
PATCH  /api/v1/memory/{id}                      edit content, or disable: {"is_enabled": false}
DELETE /api/v1/memory/{id}

POST   /api/v1/companion/conversations          optionally attach a trip_id (ownership verified)
GET    /api/v1/companion/conversations
GET    /api/v1/companion/conversations/{id}/messages
POST   /api/v1/companion/conversations/{id}/messages
                                                  -> stores your message, sends the last 12 messages
                                                     + your enabled memories to OpenAI, stores + returns
                                                     the reply
```

`OpenAIProvider` tries `AI_PRIMARY_MODEL` first and falls back to
`AI_FALLBACK_MODEL` once on failure (Blueprint §97 simplified);
raises `503 provider_unavailable` — never a fabricated reply — if
both fail or `OPENAI_API_KEY` isn't set.

**Tool calling is wired in.** Companion isn't limited to conversation
text anymore — the model can call real, read-only tools:
`get_my_profile`, `get_my_memories`, `get_trip`, `get_trip_itinerary`,
`search_hotels`, `search_flights`, `search_activities`, `get_weather`,
`convert_currency`, `geocode`, `search_places`. Two things make this
safe (Blueprint §39-40):

1. **No raw queries.** Every tool calls an existing Service
   (`TripService`, `HotelService`, etc.) — never a bespoke query
   built from LLM-supplied text.
2. **Every call re-verifies authorization independently.** The
   `user_id` used for every check comes from the server-side JWT
   (`ToolExecutionContext.user`), never from an argument the model
   supplies. If the model calls `get_trip` with a `trip_id` for a
   trip the user doesn't belong to, `TripService.get_trip_authorized`
   rejects it exactly the same way the HTTP endpoint would — the LLM
   gets `{"error": "You do not have access to this trip."}` back, not
   the data.

The loop (`CompanionService._run_with_tools`) runs at most 3
iterations of "ask the model → execute any requested tools → feed
results back," then forces a final answer with no tools offered so a
confused model can't loop forever.

**Itinerary changes now work — via propose → confirm/reject, never
directly.** This is the concrete implementation of Principle 2 ("AI
proposes, systems verify, users decide"):

```
POST /api/v1/companion/changes/{id}/confirm   applies the change (editor/owner required)
POST /api/v1/companion/changes/{id}/reject    discards it, nothing applied
GET  /api/v1/companion/conversations/{id}/changes   list proposals for a conversation
```

The model has three tools — `propose_add_trip_item`,
`propose_update_trip_item`, `propose_delete_trip_item` — and **none
of them touch the trip**. Each one only writes a
`PendingItineraryChange` row (`status=pending`) and returns its
`change_id` to the model, which the system prompt instructs it to
surface to the user as "I've proposed X — let me know if you'd like
to confirm it." Only `POST .../confirm` actually calls
`ItineraryService.add_item`/`update_item`/`delete_item` — running the
change through the *exact same* `ItineraryValidationService` checks a
manual edit would hit — and only after independently re-verifying the
confirming user has editor/owner access to the trip (never assuming
the proposer, or the conversation's owner, still has access; it's
re-checked fresh every time).

**Conversation summarization is real, not a stub.** Once a
conversation passes 24 messages, everything older than the most
recent 12 gets folded into a rolling `Conversation.summary` via one
LLM call (extending any prior summary rather than replacing it from
scratch). The summary is injected into the system prompt on every
subsequent turn so old context isn't lost even though it's no longer
sent verbatim. A summarization failure is caught and logged — it
never fails the turn whose reply was already generated and saved.

**What Companion still does NOT do** — both are DELIBERATE
architectural calls, not missed work:
- **No LangGraph.** This is a bounded, hand-rolled tool-call loop
  (max 3 iterations), not a multi-step state graph. LangGraph's main
  value over a hand-rolled loop shows up with genuinely branching,
  multi-agent, or long-running workflows — Companion's current loop
  (tool selection → execute → respond) doesn't need graph-level state
  management yet. Adopting LangGraph later is straightforward if a
  more complex workflow (e.g. the eventual Discover/Planning
  generation pipelines) needs it.
- **No separate intent-routing classifier (Blueprint §83).** OpenAI's
  native function-calling already performs practical intent routing —
  the model chooses which tool(s) a message needs, which is what a
  hand-built `GENERAL_TRAVEL` / `HOTEL_SEARCH` / `WEATHER_QUERY`
  classifier would exist to decide anyway. A parallel classification
  step would either duplicate that decision or fight with it. If a
  genuine need for the discrete category later emerges (e.g. routing
  to different system prompts per category, or analytics on query
  types), it's a small addition on top of existing tool-call
  metadata, not a rebuild.

**Automatic memory extraction is live.** After every Companion turn,
one isolated, tools-free LLM call (`app/modules/companion/
memory_extraction.py`) asks whether the exchange revealed a durable
travel preference (not a one-off trip detail or a passing mood).
Above a 0.6 confidence threshold, and only if it's not a
near-duplicate of something already remembered (simple
case-insensitive substring check), it's stored as
`source=companion_extracted` with the confidence attached — subject
to the exact same view/edit/delete/disable controls as anything the
user added manually. A failure here is logged and never breaks the
turn whose reply already succeeded.

**New required env vars used here:** `OPENAI_API_KEY` (already
present in `.env.example`; `AI_PRIMARY_MODEL`, `AI_FALLBACK_MODEL`,
and `AI_EMBEDDING_MODEL` already have sensible defaults).

### RAG — knowledge base (Phase 4, complete)

```
POST /api/v1/rag/documents   { title, source, content }
  -> chunks the content (paragraph-packed, ~1000 chars/chunk), embeds
     each chunk via OpenAI, stores KnowledgeDocument + KnowledgeChunk
     rows with a pgvector embedding column
  -> requires the `content:manage` admin permission (shared knowledge feeds
     every user's Companion, so ingestion is never open to ordinary users)

GET  /api/v1/rag/search?query=&top_k=5&source=
  -> embeds the query, over-fetches 4x top_k candidates by pgvector
     cosine distance, optionally filtered by KnowledgeDocument.source
     (metadata filtering), reranks by blending vector similarity
     (75% weight) with lexical word-overlap against the query (25%
     weight), returns the top_k after reranking with their source
     document title
```

This is also wired into Companion as the `search_travel_knowledge`
tool — the model decides on its own when a question would benefit
from a knowledge-base lookup versus answering directly.

**Reranking is real, but intentionally lightweight** — a blended
similarity+lexical score computed in Python over the over-fetched
candidates, not a separate reranking model/API call. This catches the
common failure mode of pure vector search (a chunk that's
semantically "in the neighborhood" but shares none of the query's
actual terms) without adding a second network round-trip per search.
Chunking remains paragraph-packing with no overlap or semantic
awareness. It's shared knowledge only — no user-private document
retrieval (Blueprint §34: never mix private data into shared
retrieval).

**Requires the pgvector Postgres extension** — the migration runs
`CREATE EXTENSION IF NOT EXISTS vector`, which Supabase Postgres
supports out of the box. The embedding column is fixed at 1536
dimensions to match `text-embedding-3-small` (the default
`AI_EMBEDDING_MODEL`); changing to a different-dimension embedding
model requires a new migration to alter that column.

### Phase 5 — Collaboration

```
POST   /api/v1/trips/{trip_id}/invitations       { recipient_email | recipient_username, role }   (exactly one recipient)
GET    /api/v1/trips/{trip_id}/invitations
DELETE /api/v1/trips/{trip_id}/invitations/{id}
GET    /api/v1/invitations/me                      (the invitee's own pending invitations, with the accept/reject token)
POST   /api/v1/invitations/{token}/accept
POST   /api/v1/invitations/{token}/reject

GET    /api/v1/trips/{trip_id}/members
PATCH  /api/v1/trips/{trip_id}/members/{user_id}   { role }   (owner-only)
DELETE /api/v1/trips/{trip_id}/members/{user_id}               (owner-only)

POST   /api/v1/trips/{trip_id}/comments            { content }
GET    /api/v1/trips/{trip_id}/comments
DELETE /api/v1/trips/{trip_id}/comments/{comment_id}   (author or trip owner)

POST   /api/v1/companion/changes/{change_id}/vote  { is_upvote }
```

**Invitations** carry exactly what Blueprint §44 specifies: trip,
inviter, recipient (email or @username), role, a secure token
(`secrets.token_urlsafe(32)`), a 7-day expiration, and a status.
Inviting requires editor/owner access to the trip; **you cannot
invite someone as owner** (schema-level validation rejects it —
ownership only transfers via `PATCH .../members/{id}`, not invite).
Accepting requires the authenticated user's own email to
case-insensitively match `recipient_email` — the token alone isn't
sufficient, so a forwarded invitation email can't be redeemed by the
wrong account. An expired invitation is marked `expired` and rejected
at accept-time even if no one had checked on it since.

**@username invitations**: send `recipient_username` (with or without a
leading `@`) instead of `recipient_email`. The server resolves it to an
active account, stores that account's email (so the accept gate above
is unchanged) and links `recipient_user_id`. The inviter's responses
then show the invitee's public profile (`recipient`: username, name,
avatar) and `recipient_email: null` — the email is never revealed. The
invitee lists their live invitations via `GET /invitations/me`
(`trip_title`, `trip_destination`, `inviter`, `role`, `token`,
`expires_at`) and accepts/rejects with that `token`. Inviting yourself,
someone already on the trip, or someone with a live pending invitation
is rejected (400 / 409).

**Members and comments** carry a `user` object (`id`, `username`,
`first_name`, `last_name`, `avatar_url` — never email or phone)
alongside the original `user_id`.

**Member management is owner-only**, deliberately narrower than the
"editor-or-above" gate used everywhere else in Phase 3: letting
editors change other editors' roles or remove people would be a
privilege-escalation path. Both `change_member_role` and
`remove_member` refuse to demote/remove the last remaining owner —
`ForbiddenError("A trip must always have at least one owner...")` —
so a trip can never end up ownerless.

**Comments** are open to any member (including viewers) since posting
a comment doesn't mutate the itinerary; deletion is restricted to the
comment's author or the trip owner.

**Voting is advisory only.** `PendingItineraryChange` (from Phase 4's
propose→confirm/reject flow) can now be upvoted/downvoted by any trip
member — one vote per person, upsertable. This does **not** change
who can confirm or reject a proposal: that authority stays exactly
where Phase 4 put it (editor/owner via the existing
`/companion/changes/{id}/confirm|reject` endpoints). Votes are
visible context for that human decision, not a substitute for it —
there's no auto-apply-on-majority logic here, which would have been a
much bigger (and riskier) feature to build honestly.

**Not yet implemented:** inviting someone who doesn't have a
Tour-Wayva account yet and prompting sign-up from the invitation
email — the invitation record and email are created regardless, but
acceptance still requires an existing, logged-in account.

### Phase 6 — Monetization, entitlements and feature flags

```
GET   /api/v1/plans                        list active plans
POST  /api/v1/plans                        (plans:manage) create a plan
PATCH /api/v1/plans/{id}                   (plans:manage) link a Paystack plan code / (de)activate

GET   /api/v1/subscriptions/me             your current subscription, or null
POST  /api/v1/subscriptions/subscribe      { plan_id }  FREE plans only (paid plans -> 402, use /payments/checkout)
POST  /api/v1/subscriptions/cancel         stop renewal; access continues to the end of the paid period

GET   /api/v1/trials/config                current global trial settings
PUT   /api/v1/trials/config                (plans:manage) update them
GET   /api/v1/trials/me                    your trial status, or null
POST  /api/v1/trials/start                 one-time per account (also starts automatically at activation)

GET   /api/v1/entitlements/me              your resolved feature access + which flags are globally disabled
```

Payments are Paystack (see "Payments (Paystack)"). Trial config changes don't retroactively affect trials
in progress — flags and duration are snapshotted onto each `UserTrial` when it starts. There is NO endpoint
that lets a user change their own entitlements; per-user overrides are admin-only
(`PUT /admin/users/{id}/feature-overrides`, `plans:manage`, audited).

**Feature access is decided in ONE place** — `EntitlementService` (`app/modules/entitlements`), using the pure
`rules.decide`, evaluated server-side on every request (never cached on the user, never trusted from the client).
Precedence, highest first:

1. **Global kill switch** — an admin switched the feature off for EVERYONE (incident/maintenance). Beats
   everything, including a per-user grant. Requests answer **503 `feature_unavailable`** (a temporary outage,
   not an upsell).
2. **Per-user override** — an explicit grant or revocation (a revocation also beats "open to all", so an abusive
   account can be cut off individually).
3. **Open to all** — an admin opened the feature to every user regardless of plan (launch mode, promotions).
4. **Trial → subscription → free tier** — an active trial's flags, else the active subscription's plan flags, else
   the free floor (`DISCOVER`, `PLANNER`, `WEATHER`). Trial and subscription flags are not unioned.

A plan restriction answers **403** with `details: {required_feature, reason: "not_in_plan" | "user_override_revoked"}`
so a client can tell "upgrade" from "revoked" from "temporarily down". Global toggles are cached in Redis for 15 s and
the cache is deleted the moment an admin changes one, so a kill switch is felt by every replica at once.

```
GET /api/v1/admin/feature-flags            (flags:manage) every flag with its global state
PUT /api/v1/admin/feature-flags/{flag}     (flags:manage) { is_killed?, is_open_to_all?, note? } — audited with before/after
```

**Where each flag is enforced** (a test scans the router source, so a new endpoint that forgets its gate, an
accidentally-public endpoint, or an un-rate-limited provider endpoint fails the build). Reading your OWN data is
never gated — a lapsed plan must not lock anyone out of their trips, memories or attachments:

| Flag | Enforced on |
|---|---|
| `DISCOVER` | `POST /discover/search`; Companion tool `search_destinations` |
| `PLANNER` | create trip, add/edit/delete item, restore version, notes/costs/routes, trip preferences, generate itinerary, **confirm** a Companion change; Companion propose/checkpoint tools |
| `COMPANION` | create conversation, send message, voice message |
| `VOICE` | voice messages |
| `ATTACHMENTS` | upload / confirm / link attachments, travel documents |
| `PDF_EXPORT` | export trip PDF, upload trip PDF |
| `MEMORY` | creating memories; Companion remembering (extraction) and recalling; memories used by planning and Discover. Viewing/editing/deleting memories is a privacy right and stays open |
| `HOTELS` / `FLIGHTS` / `ACTIVITIES` | the search endpoints; the matching Companion tools |
| `WEATHER` | both weather endpoints; Companion `get_weather` |
| `COLLABORATION` | creating invitations (invitees join without needing the feature) |
| `PREMIUM_AI` | selects the strong model tier for itinerary generation and revisions (others use the default model) |
| `LIVE_TRAVEL` | nothing — no live-travel feature exists yet |

The Companion enforces features **per intent** (`HOTEL_SEARCH`→`HOTELS`, ...) **and per tool** (`TOOL_FEATURES`): the agent
is only *offered* tools the user is entitled to, and `execute_tool` re-checks (the intent allowing a request is not
enough — `TRIP_QUERY` offers `get_weather`, which still needs `WEATHER`). Queued planning jobs re-check `PLANNER` when
they actually run, so a job that waited in the queue cannot run for a user whose plan lapsed or for a feature an admin
has since switched off.

### Phase 7 — Admin

```
POST   /api/v1/admin/admins                    { user_id, role }   (super admin only)
GET    /api/v1/admin/admins                     any active admin may view the roster
DELETE /api/v1/admin/admins/{user_id}                                (super admin only)

GET    /api/v1/admin/users?query=&status=&limit=&offset=            (permission: users:view)
GET    /api/v1/admin/users/{user_id}             includes subscription/trial status
POST   /api/v1/admin/users/{user_id}/block       { reason }         (permission: users:block)
POST   /api/v1/admin/users/{user_id}/unblock                        (permission: users:block)
DELETE /api/v1/admin/users/{user_id}             { reason }         (permission: users:delete)
POST   /api/v1/admin/users/{user_id}/revoke-sessions                (permission: users:manage_sessions)

POST   /api/v1/admin/messages     { recipient_user_id, message, channel }  (permission: messaging:send)
GET    /api/v1/users/me/admin-messages           any user's own inbox — no admin permission needed

POST   /api/v1/admin/broadcasts   { segment, message, channel }     (permission: broadcast:send)
GET    /api/v1/admin/broadcasts/{id}             check job status/recipient_count

GET    /api/v1/admin/audit-logs?limit=&offset=   (permission: audit:view)
GET    /api/v1/admin/dashboard                   (permission: analytics:view)
```

**RBAC is database-driven** (`admin_permissions`, `admin_roles`, `admin_role_permissions`,
`admin_user_roles`). What stays in code is the *catalog* of permissions the endpoints actually check
(`users:view`, `users:block`, `plans:manage`, `payments:view`, ...) — a permission nothing enforces
would be meaningless, and a test scans the source so a typo can never slip through. Roles are composed
from that catalog and live in the database:

- The 7 roles of Blueprint §55 are **system roles** (seeded; cannot be deleted or renamed); a Super Admin
  can create custom roles and edit a role's permissions at runtime. An admin may hold **several roles**;
  permissions are the union. A role flagged `is_super` implies every permission (so a new permission never
  has to be remembered for the Super Admin) and cannot be edited.
- Checks read the database on every request, so a role edit or an admin change applies on the very next
  request — nothing is cached.
- **Only a Super Admin can manage admins and roles** (not a delegable permission), which trivially satisfies
  Blueprint §54 ("never allow normal admins to create Super Admins").
- Safety rails: the **last active Super Admin can never be demoted or disabled**; nobody can disable their own
  admin access; a role still held by an admin cannot be deleted; only catalog permissions can be granted; an
  admin always keeps at least one role.
- **Every denied attempt is audited** (`permission.denied`, committed before the 403), as is every roster and
  role change (with before/after).
- **The audit log is append-only in the database too**: a trigger rejects UPDATE, DELETE and TRUNCATE on
  `admin_audit_logs` (migration `0017`). (Test suites that truncate tables between tests must not include it.)
- New permission in code → `sync_rbac_catalog` (run at API start-up and by `python -m scripts.sync_rbac`) inserts
  it and grants it to the system roles that default to it. It never touches an existing role's permissions, so
  a Super Admin's edits survive every later sync.
- **Bootstrap** the first Super Admin in any environment with
  `python -m scripts.create_super_admin --email you@example.com` (needs database access; audited).

```
GET    /api/v1/admin/me                     your roles + effective permissions
GET    /api/v1/admin/permissions            the catalog
GET    /api/v1/admin/roles                  roles with permissions and holder counts
POST   /api/v1/admin/roles                  (Super Admin) create a custom role
PATCH  /api/v1/admin/roles/{id}             (Super Admin) description / REPLACE permissions
DELETE /api/v1/admin/roles/{id}             (Super Admin) custom, unassigned roles only
POST   /api/v1/admin/admins                 (Super Admin) { user_id, roles: [...] }
PUT    /api/v1/admin/admins/{user_id}/roles (Super Admin) replace an admin's roles
DELETE /api/v1/admin/admins/{user_id}       (Super Admin) disable
```

**Session revocation is a real, working mechanism, not a stub.**
`User.sessions_invalidated_at` is checked against every access
token's `iat` claim in `get_current_user`
(`app/api/dependencies.py`) — a token issued before a revocation is
rejected with 401 even though it hasn't expired. This is what "revoke
sessions" (§51, §77) means for a stateless-JWT system with no
separate session store.

**Broadcasts are a genuine background job, not a same-request loop.**
`POST /admin/broadcasts` only writes a `BroadcastJob` row and calls
`.delay()` — it returns `202 Accepted` immediately. The actual
segment resolution (`all` / `free` / `trial` / `premium` / `inactive`)
and per-recipient sending happens in `app/workers/broadcast_tasks.py`,
which runs in the separate `worker` container from
`docker-compose.yml`. Segment resolution checks subscription/trial
status per user rather than one batched SQL join — documented as a
straightforward-not-optimized choice, fine for moderate user counts,
with batching flagged as a Phase 8 hardening item if it's ever needed.

**Audit logging is append-only by construction, not by policy
statement.** `AdminRepository` exposes `create_audit_log` and
`list_audit_logs` — there is no update or delete method anywhere in
the codebase that touches `admin_audit_logs`. That's what "protected
from ordinary modification" (§56) means without a separate
database-permission layer.

**User deletion here is a soft-delete**, flipping the same
`status`/`is_active` fields `block_user` does, just to `DELETED`
instead of `SUSPENDED`. The full account-deletion workflow Blueprint
§77 describes — anonymizing data, removing private files and
embeddings — stays a further increment; this is the admin-triggered
status change, not the complete data-lifecycle handling.

**Analytics is partial.** `GET /admin/dashboard` returns real counts
(users by status, active subscriptions, active trials) straight from
the database — no placeholder numbers. AI-cost tracking, provider
usage, and revenue analytics (§57-58) stay with `app/modules/analytics`
(still scaffolded).

### Discover

```
POST /api/v1/discover/search
  { budget_amount, budget_currency, origin?, continent?, country_region?,
    start_date?, end_date?, duration_days, travelers, travel_style?,
    interests[], climate_preference?, accommodation_preference?,
    transportation_preference?, max_results }
  -> AI-proposed, geocoding-verified destination recommendations
```

The full Blueprint §10-12 pipeline, for real:

1. **Cache lookup** (Redis, 6-hour TTL, keyed on normalized request params) — reuse before regenerate.
2. **AI candidate generation** — one LLM call proposes up to `max_results` real destinations matching the constraints, each with a rough per-person-per-day USD cost estimate and a best-travel-period guess. This is Principle 2's "AI proposes" half.
3. **Per-candidate verification** — this is the "systems verify" half:
   - **Geocoded** via `GeocodingService`; any candidate that doesn't resolve to a real place is **silently dropped**, never shown with fabricated coordinates.
   - **Weather** via `WeatherService.get_forecast` (approximate high/low range).
   - **Currency-converted cost** via `CurrencyService` (USD estimate → the traveler's `budget_currency`).
   - **Image** via `ImageService` (Unsplash, with attribution).
   - **Nearby activities** via the Amadeus activity provider.
   - Any one of these failing doesn't drop the candidate — each is wrapped independently and just leaves that field empty, so one flaky provider doesn't sink an otherwise-good recommendation.
4. **Scoring** (`app/modules/discover/scoring.py`, pure functions, independently unit-tested) — a weighted blend of budget fit (60%) and interest-keyword match (40%), sorted descending.
5. **Persisted** as `DiscoverySearch` + one `DiscoveryResult` row per candidate — a durable snapshot of what the person actually saw.

**Every cost figure is explicitly an estimate.** `CostBreakdown.source` is always `"estimated"` — the AI's one estimated total is split into accommodation/food/transport/activities shares by a fixed proportion (40/25/20/15), never presented as independently-verified or live pricing (Blueprint §12: "Never fabricate live prices... Clearly distinguish Estimated/Cached/Live/Provider-sourced").

Each result also carries a `trip_planning_cta` object — a pre-fillable payload matching `POST /trips`'s shape, so a client can turn "I like this destination" directly into trip creation without re-typing anything.

Gated on the `DISCOVER` feature flag (in the free tier by default) and rate-limited (10 searches / 5 min / user) since each search makes a real LLM call plus several provider calls.

### Signup field validation

- **Username** — 3–20 chars, lowercase letters/numbers/underscores,
  must start with a letter, no consecutive underscores, reserved
  names blocked (`admin`, `wayva`, `support`, etc.).
- **Phone number** — validated with Google's `phonenumbers` library
  (the engine behind Android's dialer), so it's correctly validated
  for *every* country's numbering plan, not just pattern-matched.
  Accepts full international format (`+2348012345678`) and normalizes
  to E.164 for storage.
- **Password** — 8+ characters, at least one uppercase letter and one
  digit; must match `confirm_password`.

---

## Setup — local development

### 1. Prerequisites
- Python 3.12+
- Docker + Docker Compose (recommended path)
- A Brevo account + API key (for OTP emails)
- A Cloudinary account (for avatar/destination image uploads)
- A Supabase project (Postgres + Storage) — or use the bundled local
  Postgres via docker-compose for pure local dev

### 2. Configure environment
```bash
cp .env.example .env
# then fill in: SECRET_KEY, BREVO_API_KEY, CLOUDINARY_*, SUPABASE_*
```

### 3a. Run with Docker Compose (recommended)
```bash
docker compose up --build
```
This starts the API (`:8000`), a Celery worker, local Postgres
(pgvector-enabled image), and Redis. Migrations run automatically on
container start.

### 3b. Run locally without Docker
```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

alembic upgrade head
uvicorn app.main:app --reload
```

### 4. Verify it's running
```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready   # checks DB + Redis connectivity
```
Interactive API docs: `http://localhost:8000/docs`

### 5. Run tests
```bash
pytest
```

---

## Key engineering principles carried through this delivery

- **Never trust the frontend** — `get_current_user` independently
  decodes/verifies the JWT server-side on every request.
- **Provider abstraction** — storage and email are both behind
  interfaces (`StorageProvider`, `EmailProvider`); swapping Cloudinary,
  Supabase Storage, or Brevo for another vendor means writing one new
  class, not touching callers.
- **No placeholder logic that pretends to work** — if a provider
  isn't configured, it raises `ProviderUnavailableError` (HTTP 503)
  rather than fabricating a fake URL or fake success.
- **Secrets stay server-side** — Supabase service-role key, Cloudinary
  API secret, and Brevo API key are only ever read from environment
  variables in provider code, never returned to the client.
- **UUID public IDs** — no sequential integer IDs are exposed.

---

## Phase 8 — Production hardening

```
Rate limiting applied to:
  POST /auth/signup            5 / hour / IP
  POST /auth/login              10 / 5 min / IP  (plus failed-login lockout below)
  POST /auth/resend-otp          3 / 5 min / IP
  POST /auth/forgot-password     3 / 15 min / IP
  POST /companion/.../messages  20 / min / user  (the AI-costing endpoint)
  /uploads/*                    20 / 5 min / user (router-wide)
  /admin/*                      60 / min / user  (router-wide)
  POST /admin/broadcasts         5 / hour / user (stricter, on top of the router-wide limit)
```

**Real Redis-backed limiter, not a decorator that does nothing.**
`app/core/rate_limit.py` implements a fixed-window counter
(`INCR` + `EXPIRE` on a `ratelimit:{bucket}:{window}` key) and a
`rate_limit()` FastAPI dependency factory
(`app/api/dependencies.py`) that wires it into any endpoint or
router. Like `CacheService`, it **fails open** if Redis is
unreachable — a rate limiter that takes the whole API down when its
own backing store hiccups would be worse than no rate limiter.

**Failed-login lockout is real too.** Five failed login attempts for
the same email within 15 minutes locks further attempts out — even
with the *correct* password — until the window expires, mirroring
real brute-force protection (`app/modules/auth/service.py`, backed by
the same `app/core/rate_limit.py`).

**Security headers** (`X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, and `Strict-Transport-Security`
when `ENVIRONMENT=production`) are added by a middleware in
`app/main.py` on every response.

**Mock providers exist for testing (Blueprint §92)** —
`app/providers/llm/mock_provider.py` and
`app/providers/email/mock_provider.py` are real, working,
network-free implementations of the `LLMProvider`/`EmailProvider`
interfaces (scripted responses, in-memory sent-email log). The same
pattern — implement the interface, return deterministic canned data —
extends to any other provider a future test needs; these two are
built as concrete examples rather than stubbing all fourteen
providers speculatively.

**Dev seed script** (`scripts/seed_dev_data.py`) creates a
SUPER_ADMIN account, Free/Premium plans, and a few sample places —
`python -m scripts.seed_dev_data` from the project root. Explicitly
dev/staging-only; never run it against production.

**On "full test suite run against a real database":** this delivery
environment has no network access and no live Postgres/Redis to
actually run `pytest` against. What I *did* do throughout — not just
claim — is hand-execute every unit test that has no database
dependency directly in this sandbox and confirm the assertions pass
(cache-key builders, phone/username validation, itinerary validation
logic, RAG chunking and hybrid-reranking scoring, admin permission
matrix, mock providers). Tests that need SQLAlchemy/Postgres are
included and ready to run (`pytest` from the project root once
dependencies are installed and `DATABASE_URL` points at a real
database — `docker compose up` gets you both) but were not executed
in this environment. That's a real gap, stated plainly rather than
glossed over.

## What's still genuinely missing

Verified by reading the code; nothing below has been executed against a live
database (the sandbox this was built in has no network, so PostgreSQL/Redis
could not be started). Remaining work, in build order:

1. **Executing the test suite** against PostgreSQL + Redis (Docker Compose),
   plus API, integration and security tests.

Known limitations of the pieces delivered here:
- **Itinerary generation**: the model's non-provider prices are estimates and are labelled
  `source=estimated`. Opening-hours verification now runs for timed activities/attractions/
  restaurants — Amadeus Tours & Activities has no structured opening hours, so this uses
  OpenStreetMap's `opening_hours` tag via the keyless Overpass API (a new provider outside the
  Master Prompt's original list, added because the alternative was not implementing it at all; see
  "Opening-hours verification" below). The LangGraph wiring is exercised by the real library only
  when it is installed (tests skip otherwise) — the same graph spec is unit-tested with an in-repo
  runner.
- **Flights**: adding a searched offer to a trip (`POST /trips/{trip_id}/flights`) now attempts a
  live reprice against Amadeus's Flight Offers Price API before saving (using the raw offer
  cached 10 min at search time); it falls back to the client-supplied snapshot — honestly marked
  `source=client_snapshot`/`price_verified=false` — only when that can't be confirmed. There is
  no day-by-day AI planning of flights (they are trip-level bookends, added explicitly, not
  generated).
- **Paystack**: the `subscription.create` email token is parked in an event only when it arrives
  before the first charge, and is erased once attached. Dispute *resolutions* are recorded but a
  person must re-activate a customer whose dispute was decided in the merchant's favour, because
  Paystack's resolution vocabulary is not something to auto-restore access from. A refund reversed
  by Paystack after `refund.processed` is not modelled. The webhook IP list is Paystack's currently
  published set and must be re-checked if they change it.
- The deprecated `/uploads/attachment` and `/uploads/travel-document` endpoints still exist for
  API compatibility; new clients should use `/attachments/upload?category=...`. Rows created
  before migration `0023` are backfilled to `attachment`, except existing trip PDFs (identified by
  their `trips/` storage prefix), which become `trip_pdf`.
- Legacy `.doc`/`.xls`/`.ppt` (OLE) files are parsed via `olefile` to read the
  root storage CLSID, so they're now correctly distinguished from each other
  (an Excel workbook is no longer mislabeled and accepted as `application/msword`
  — see `app/utils/files._sniff_ole_subtype`), and any OLE container with a
  `Macros`/`VBA` storage is rejected regardless of its CLSID. Independent of
  the global `REQUIRE_MALWARE_SCAN` setting, this format family now *always*
  requires a reachable ClamAV scanner and is rejected without one (fail
  closed) — `docker compose up` runs a ClamAV sidecar by default so this is
  satisfied out of the box; see `.env.example`'s `CLAMAV_HOST`.
- Access tokens are invalidated immediately on single-session revoke
  (`DELETE /users/me/sessions/{id}`) and on `POST /auth/logout`, not just on
  `POST /auth/logout-all`/password change (which already worked via the
  per-user `sessions_invalidated_at` cutoff). Every access token carries a
  `sid` claim; revoking gets that one session's id blocklisted in Redis with
  a TTL equal to the access-token lifetime — see `app/core/session_blocklist.py`.
  This degrades gracefully if Redis is unreachable (the session ROW is still
  revoked; only the already-issued token's immediate kill switch is
  unavailable, so it falls back to expiring naturally).
- A token issued in the same wall-clock second as a "revoke all" call can
  survive it (JWT `iat` has one-second resolution).
- The 196-country localization dataset (`app/core/country_data.py`) is a best-effort single
  currency/language/timezone per country, not an exhaustive statement — many countries have
  multiple official languages or timezones; the most common/capital one is used.
