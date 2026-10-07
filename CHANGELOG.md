# Changelog

## Sign-up: phone availability and country detection

No migrations.

### Added
- **`GET /auth/phone-available?phone=+234...`** (public, 30/min per IP). Validates and normalizes the number, then says whether
  an account already uses it (`{phone_number, available, reason}`), exactly like `/auth/username-available`. `/auth/signup`
  and `/auth/google/complete` still re-check on the server.
- **`GET /auth/detect-region`** (public, 30/min per IP). Country-level only: IPinfo country of the request IP, then the
  Accept-Language region subtag, else `country: null`. Returns `{country, country_name, calling_code, source}`; never a city,
  coordinates or the IP. The app calls it on first load so the sign-up phone field defaults to the visitor's dialling code.
- `calling_code_for_region()` in `app/utils/phone.py`. Tests: `tests/unit/test_phone_and_region.py`; both routes are listed as
  intentionally public in `tests/unit/test_feature_gating.py`.

## Streaming Companion replies

### Added
- **`POST /companion/conversations/{id}/messages/stream`** (Server-Sent Events). Same turn, rules, rate limit and flags as
  the normal send route, but the reply arrives as it is written. Events: `status` (`thinking` | `tool`), `delta` (text),
  `reset` (discard the text shown so far: it was only a preamble to a tool call), `done` (the saved `MessageResponse`,
  authoritative) and `error` (`error_code`, `message`; internals never leak). A comment keepalive is sent every 15 s; if
  the client disconnects the turn is cancelled. The turn runs on its own database session (FastAPI closes the request's
  session before a streamed body is sent). Summary and memory extraction run after the stream ends.
- **`LLMProvider.generate_stream(on_delta=...)`**: real token streaming in `OpenAIProvider` (`stream_options.include_usage`
  so token accounting stays exact); the default implementation forwards the finished answer once, so other providers and
  test doubles keep working. `StreamAccumulator` is a pure, unit-tested fold of the chunks (text, tool calls whose
  arguments arrive in pieces, usage). One attempt per model: if the primary fails before any text was shown we fall back
  to the secondary model; once text is on screen we fail with a clear message instead of showing two answers.
- `CompanionPorts.llm_stream`: the agent loop streams every model call it makes (tool-calling rounds and the final answer).
  Tests: `tests/unit/test_llm_streaming.py`.

## Structured chat cards, photos, spoken replies, destination guides, trip editing, minimum budget

One new Alembic migration: `0027_message_meta`. Run `alembic upgrade head`.

### Added
- **`messages.meta` (JSONB)** and `meta` on `MessageResponse`: `{"trip_ids": [...], "images": [...]}`. Trip tools
  (`get_trip`, `get_trip_itinerary`, `get_trip_history`, last-trip lookup) record which of the user's trips an answer drew
  on, so the app can show trip cards without guessing from text. NULL for old messages and plain answers.
- **Companion tool `show_place_photos`** (1-4 distinct places): real photos from the image provider via the cached
  ImageService (never from the model), de-duplicated, attribution fields included. Allowed only for general/discovery/
  trip/activity/history intents; read-only. Tests: `tests/unit/test_companion_cards.py`.
- **`POST /companion/messages/{message_id}/speech`** returns MP3 of one of the caller's own assistant replies (OpenAI TTS
  behind a provider interface; markdown reduced to plain speech, capped at `TTS_MAX_CHARS`). A message id, never free text,
  so it cannot be used as a general TTS API; audio is never stored. 503 `provider_unavailable` when not configured. 60/h/user,
  VOICE + COMPANION flags. New settings: `TTS_MODEL`, `TTS_VOICE`, `TTS_MAX_CHARS`. Tests: `tests/unit/test_speech_service.py`.
- **`POST /destinations/guide`** `{name, country?}`: AI-written guide (overview, best time, suggested stay, rough daily budget
  in USD, highlights, languages, currency, tips) for ANY place. The place is verified with the geocoder first (404 otherwise),
  the model output is validated/clamped field by field, results are cached 30 days, 20/h/user, DISCOVER flag; labelled
  `source: "ai"` with a disclaimer. Tests: `tests/unit/test_destination_guide.py`.
- **`PATCH /trips/{trip_id}`** edits title, origin, travelers, budget (always) and destination/dates (only while there is no
  itinerary and no generation running; 409 `itinerary_exists` otherwise; changing dates on an empty draft re-creates its days).
  Tests: `tests/unit/test_trip_update.py`.
- **Server-side minimum trip budget** (`MIN_TRIP_BUDGET_USD`, default 100, 0 disables) on trip create/update and Discover search:
  422 `budget_too_low` with `details.min_amount/currency`. Converted with the same two-significant-digit rounding the app shows;
  fails open if the exchange rate is unavailable. Tests: `tests/unit/test_budget_policy.py`.

### Note (correction)
- Hotel search already resolves the IATA city code itself when none is given (Amadeus location lookup, cached) inside the
  planning workflow, so no endpoint was needed. Earlier notes claiming otherwise were wrong; the web app now says so.

## Cancel generation, notification management, login by username, phone change

### Added
- **`POST /trips/{trip_id}/generate/{job_id}/cancel`** stops a queued or running generation (editor/owner only,
  idempotent, 30/h/user). The request is stored under its own Redis key and checked before each workflow step, so it
  takes effect at the next step boundary; nothing is persisted before the last step, so the trip is left unchanged.
  New job status `cancelled`. A model call already in flight finishes first but its result is discarded.
  Tests: `tests/unit/test_planning_cancel.py`.
- **Notifications:** `POST /notifications/read-all` -> `{updated}`, `DELETE /notifications/{id}` (204; 404 for unknown or
  someone else's), `DELETE /notifications` -> `{deleted}`. Ownership is part of the SQL.
- **Login by email OR username:** `POST /auth/login` accepts `identifier` (email, or username with/without a leading @);
  `email` still works. One error message for "no such account" and "wrong password". Failed attempts are counted
  against the typed identifier AND the account's email so alternating forms cannot double the guesses.
  Tests: `tests/unit/test_login_identifier.py`.
- **Phone number change (OTP-verified):** `POST /users/me/phone/change-request` (new number -> a code is emailed to the
  account email, 5/h) and `POST /users/me/phone/change-confirm` (number + code -> updated profile, 10/15 min). We have
  no SMS channel, so the emailed code proves account ownership; the number is re-validated and re-checked for
  uniqueness at confirm, and the unique index remains the final guard. Uses the existing `phone_verification` OTP
  purpose (no migration). `PATCH /users/me` still rejects `phone_number` on purpose.
  Tests: `tests/unit/test_phone_change_schema.py`.

## Welcome notification and email

### Added
- **A welcome in-app notification and a branded welcome email**, sent once when an account first becomes usable: after
  email OTP verification and after Google sign-up completion (`app/modules/notifications/welcome.py`). Localized for
  en/es/fr/it/pt/de/ru/pl/zh/ar (anything else falls back to English); all dynamic values are HTML-escaped; the button
  only appears when `FRONTEND_URL` is set to an http(s) URL. The in-app row is written first; the email has a 6 s
  timeout. Entirely best effort: any failure is logged and swallowed, so it can never block or fail sign-up.
  The notification is a `system_announcement` and feeds the app's bell badge.
- `FRONTEND_URL` setting (optional) in `app/core/config.py` and `.env.example`.
  Tests: `tests/unit/test_welcome.py`.

## Companion: message order and faster replies

### Fixed
- **Replies could be listed above the question they answered.** `messages.created_at` defaults to the database's `now()`,
  which is the START of the transaction, so a user message and its reply saved in one request got identical timestamps
  and came back in either order (it also scrambled the history sent to the model). New messages now get explicit
  timestamps (the reply is always strictly later, `reply_timestamp()`), and listing breaks ties by role (user before
  assistant) so rows written before this fix sort correctly too. Tests: `tests/unit/test_companion_ordering.py`.

### Changed
- **Faster replies.** `POST /companion/conversations/{id}/messages` no longer waits for the rolling summary and the memory
  extraction (two extra model calls that do not change the reply): they run after the response is sent, on their own
  database session (`CompanionService.post_turn`, scheduled with FastAPI `BackgroundTasks`; failures are logged, never
  surfaced). The voice endpoint still runs them inline.

## Real generation progress

### Added
- **`progress` on generation jobs** (`GET /trips/{id}/generate/{job_id}` and the 202 response):
  `{stage, stage_index, stage_count, percent}` with stages `locate -> gather -> currency -> draft -> verify -> save`
  (then `done`, 100%). Each stage is entered by a real workflow node, so it only advances when real work finishes;
  `percent` is the share complete when the stage began. `null` for jobs created before this change and while queued.
  Best effort: a Redis failure while recording progress never fails the generation.
  Tests: `tests/unit/test_planning_progress.py`.

## Location autocomplete

### Added
- **`POST /api/v1/location/suggest`** `{query, limit}` -> `{results:[{formatted_address, latitude, longitude, country, city, region}], source}`.
  Powers every location dropdown in the frontend (header location, Traveling from, trip start/destination).
  Place-level results only (roads/buildings filtered), de-duplicated, cached per normalised query (same TTL as
  geocoding), rate limited at 240 / 5 min / user. Returns `[]` rather than 404 when nothing matches.
- `GET /weather/current` now also returns `feels_like_c` (WeatherAPI `feelslike_c`; null when unavailable or when served
  from a cache entry written before this change).
- `GeocodingProvider.search_places()` (default falls back to the single best match, so mock/other providers keep
  working) and an OpenCage implementation; `parse_suggestions()` is a pure function with unit tests
  (`tests/unit/test_location_suggest.py`).

## Backend-authoritative trip status, per-member archive, Google-connected flags

One new Alembic migration: `0026_trip_member_archive`. Run `alembic upgrade head`.

### Added
- **`display_status` and `bucket` on every trip response**, computed on the backend
  (`app/modules/trips/display_status.py`) so the two status lists in the frontend brief map onto one
  vocabulary: `draft | generating | failed | ready | upcoming | active | completed | cancelled | archived`
  (frontend "Planning" = `generating`). Driven by the raw `status`, the trip dates in the viewer's
  timezone, the itinerary-generation job, and the viewer's archive flag. `TRIP_UPCOMING_WINDOW_DAYS`
  (default 30) separates `upcoming` from `ready`.
- `generation` on trip responses (`job_id`, `state`, error fields) so the UI can show "generating" /
  "failed" and resume polling after a reload; `updated_at` for "last updated".
- **Archive**: `POST /trips/{id}/archive` and `/unarchive` (per member — archiving only hides the trip
  for you) and `GET /trips?bucket=all|drafts|upcoming|active|completed|archived`. Migration `0026` adds
  `trip_members.archived_at`.
- `/users/me` now reports `google_connected`, `has_password` and `sign_in_methods`.

### Changed
- `GET /trips` without `bucket` returns every trip you have not archived (nothing is archived until you
  archive something, so existing behaviour is unchanged).
- The Redis job record now also keeps a pointer to each trip's latest job (`planning:latest:{trip_id}`).

## IP-based approximate location for the dashboard weather widget

No migration. Set `IPINFO_TOKEN` (city-level coordinates depend on your IPinfo plan; without them
the city is geocoded, and without a token the account's country is used).

### Added
- **`GET /location/approximate`**: the caller's approximate location from their IP address —
  `latitude`, `longitude`, `city`, `region`, `country`, `country_name`, `timezone`, `source`
  (`"ip"` | `"account_country"`), `approximate: true`. Falls back to the country the account was
  localized to when the IP can't be located (e.g. `127.0.0.1` in local development, a VPN, IPinfo
  down); `422 location_unavailable` if nothing resolves. The IP itself is never returned.
- `IPinfoProvider.lookup_location` (Redis-cached, also negative results; private/loopback
  addresses are never sent to IPinfo) and `app/modules/location/` (`ApproximateLocationService`).

### Changed
- **`GET /weather/current`**: `latitude`/`longitude` are now optional. Omit both and the location is
  approximated as above; the response gains `location_source` (`"explicit"` | `"ip"` |
  `"account_country"`), `city`, `region` and `country` for the widget label. Sending only one of the
  two is a 422. Existing calls with both coordinates behave as before.
- Behind a load balancer set `TRUSTED_PROXY_HOPS` correctly, or every request appears to come from
  the proxy's address and the IP location will be wrong.

## Frontend-readiness API fixes: item media, member/comment identity, @username invites

One new Alembic migration: `0025_invitation_recipient_user`. Run `alembic upgrade head`.

### Added
- **`@username` invitations**: `POST /trips/{id}/invitations` accepts `recipient_username` (or
  `recipient_email` — exactly one). The invitee's email is never shown to the inviter; the
  response carries their public profile instead. New `GET /invitations/me` lists the signed-in
  user's pending, unexpired invitations (by username link or email) with the `token` that
  accept/reject take — previously the invitee had no way to obtain it. Inviting yourself, an
  existing member, or someone with a live pending invitation is now rejected. Migration `0025`
  adds `trip_invitations.recipient_user_id`.
- **Member and comment identity**: `TripMemberResponse` and `CommentResponse` now include
  `user` (`id`, `username`, `first_name`, `last_name`, `avatar_url`), loaded with one batched
  query per response (`app/modules/collaboration/presenters.py`).

### Changed
- `TripItemResponse` now includes `image_url`, `booking_link` and `source`; `TripResponse` now
  includes `overview`. (Item write requests still cannot set `image_url`/`booking_link`.)
- `InvitationResponse.recipient_email` is now optional (null for @username invites); the
  duplicate `TripMemberResponse` in `trips/schemas.py` is now a re-export of the collaboration one.

## CI/CD, admin cost/revenue analytics, and item-5 hardening fixes

Two new Alembic migrations: `0023_analytics_cost_and_uploads` and
`0024_payment_refunds_disputes`. Run `alembic upgrade head` before deploying this version.

### Added
- **CI/CD**: `.github/workflows/ci.yml` (lint/compile, unit tests, integration+API tests against
  real Postgres+pgvector/Redis service containers, a security-tagged test job, Docker build
  validation) and `cd.yml` (tag-triggered build/push to GHCR); `dependabot.yml`, PR template,
  `pyproject.toml` ruff config.
- **Admin cost/revenue dashboard completeness** (migration `0023`): `ai_usage_records.trip_id`
  enables real cost-per-trip (not just cost-per-user); new `GET /admin/analytics/ai-usage/by-user/{id}`
  and `/by-trip/{id}`. Cache hit/miss instrumentation in `CacheService` (per provider-category
  counters in Redis) backs a new `GET /admin/analytics/cache` (hit rate + estimated $ saved) and
  `POST /admin/analytics/cache/reset`. New `GET /admin/analytics/revenue`: real MRR (from active
  subscriptions, by plan currency), churn rate, trial-conversion rate, gross revenue (from
  verified Paystack payments), and an explicitly-labelled ESTIMATED affiliate-revenue figure fed
  by a new `booking_clicks` table + `POST /trips/items/{item_id}/booking-click` (fired when a
  user follows a trip item's `booking_link`).
- **Flight offer live re-verification**: `search_flights` now caches each raw Amadeus offer
  (10 min TTL, keyed by offer id); adding a flight to a trip
  (`POST /trips/{trip_id}/flights`) attempts a live reprice via Amadeus's Flight Offers Price API
  before saving. Success → `source=provider`, provider-confirmed price. Can't be confirmed
  (cache expired / Amadeus unreachable) → still added from the client snapshot, but honestly
  marked `source=client_snapshot` with `price_verified=false` and a `verification_note` in the
  response, instead of silently claiming a live guarantee that was never checked.

- **Paystack refunds and chargebacks** (migration `0024`): new `POST /admin/payments/{id}/refund`
  (new `payments:refund` permission, finance admin + super admin only, rate-limited, audit-logged,
  never auto-retried) asks Paystack to refund; because refunds are asynchronous, the payment only
  changes when the signed `refund.processed` webhook arrives — partial refunds are tracked in
  `refunded_amount_minor`, a full refund marks the payment `refunded`, ends the subscription it was
  funding and disables it at Paystack (only when it is the payment funding the current period).
  `refund.failed`, `charge.dispute.create` (payment → `disputed`, access revoked immediately,
  critical security event) and `charge.dispute.resolve` (recorded, never auto-restores access) are
  handled too. Revenue analytics are now net of refunds. New `PaymentProvider.refund_transaction`
  (Paystack + mock).
- **Paystack webhook source-IP allow-listing** (`PAYSTACK_WEBHOOK_IP_ALLOWLIST`,
  `PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST`): checked after the signature; flag-only by default,
  403 when enforced.

- **One tracked upload path** (migration `0023`): `attachments.category` (`attachment` |
  `travel_document` | `trip_pdf`). `/uploads/attachment`, `/uploads/travel-document` and
  `/uploads/trip-pdf/{id}` used to store files with no database row; they now delegate to
  `AttachmentService` (the first two are marked deprecated in favour of
  `POST /attachments/upload?category=...`), and the duplicated `UploadService.upload_attachment` /
  `upload_travel_document` were removed. Also fixes a latent bug: every row's signed URL was minted
  against the attachments bucket, so generated trip PDFs got a URL for the wrong bucket — rows are
  now routed by category (existing trip PDF rows are backfilled from their `trips/` key prefix).
  Travel documents are stored privately and are deliberately not text-extracted. `GET /attachments`
  gains a `category` filter and every attachment response a `category` field.

- **Opening-hours verification** (Master Prompt §16, §26): new `OpeningHoursProvider` interface,
  an OpenStreetMap/Overpass implementation (`app/providers/opening_hours/`, keyless, 7-day cache,
  conservative name-matching so hours are only attached when the venue name actually agrees) and a
  new narrow, well-tested `opening_hours` syntax parser (`app/utils/opening_hours.py`) that refuses
  to guess at anything outside its supported subset. Wired into itinerary generation and revisions
  as a `opening_hours_conflict` WARNING (never a hard error, one model-repair attempt, same pattern
  as the existing weather conflict), capped/time-boxed/concurrent so a slow Overpass degrades to
  "no data" rather than failing generation. New settings: `OPENING_HOURS_PROVIDER` (default
  `overpass`, or `none` to disable) and related tuning knobs.

### Changed
- `AmadeusClient` gained `post_json` (JSON-body POST, needed for Flight Offers Price — GET-only
  previously).
- **Legacy OLE upload safety**: `sniff_content_type` no longer labels every OLE Compound File as
  `application/msword`. It now reads the container's root storage CLSID (via the new `olefile`
  dependency) to correctly tell Word/Excel/PowerPoint apart — closing a real gap where an Excel
  workbook was silently accepted as if it were a Word document — and rejects any OLE container
  with a `Macros`/`VBA` storage or an unrecognized CLSID outright (never falls back to a "probably
  Word" guess). `UploadService._scan` now requires a reachable malware scanner for this format
  family unconditionally, regardless of the global `REQUIRE_MALWARE_SCAN` setting. `docker-compose.yml`
  now runs a ClamAV sidecar by default so this requirement is met out of the box.
- **Single-session revocation is now immediate**: new `app/core/session_blocklist.py`
  (Redis, keyed by the access token's `sid` claim, TTL = access-token lifetime) is checked in
  `get_current_user` and populated by `UserService.revoke_session` (`DELETE /users/me/sessions/{id}`)
  and `AuthService.logout`. Previously only user-wide events (logout-all, password change, admin
  "revoke all sessions") invalidated already-issued access tokens immediately; a single revoked
  device's access token stayed valid until its natural ≤30-minute expiry.


### Added
- **Saved places**: `saved_places` table (migration `0022`, RLS-covered), `POST/GET/DELETE
  /saved-places`, idempotent by (user, name, ~100m); Companion `get_saved_places` / `save_place`
  tools, ungated (free tier).
- **Localization overhaul**: 196-country dataset (`app/core/country_data.py`, replacing ~20
  entries); `Accept-Language` header now actually parsed and used (previously documented but
  unwired) as the highest-priority country signal (region subtag) and for language specifically;
  IPinfo promoted to a real, resilient, Redis-cached provider
  (`app/providers/geolocation/ipinfo_provider.py`).
- **Suspicious-login analysis**: login-IP country vs. account country, informational only (never
  blocks login), `SecurityEvent` + `SECURITY_ALERT` notification, toggle via
  `SUSPICIOUS_LOGIN_DETECTION`.
- **Hotel replacement** (`PUT /trips/items/{item_id}/replace-hotel`, Companion `replace_hotel`):
  deterministic swap using a freshly re-priced live offer (Amadeus can now fetch a single hotel by
  id), converted to the trip's currency with a real rate, new itinerary version. No AI call.
- **Flights**: `POST /trips/{trip_id}/flights` + Companion `add_flight_to_trip` add a specific,
  already-searched offer to a trip day as a verified item, validated structurally and against the
  day's date (flight offers cannot be re-fetched by id the way hotels can, so this trusts the
  just-searched offer rather than re-verifying live).
- **IATA city-code auto-resolution** for hotel search during itinerary generation (previously
  silently skipped hotels if the client omitted a code); **hotel image enrichment** for the top 3
  generated-itinerary hotel candidates (Discover-style Unsplash lookup).

### Fixed
- `Accept-Language` was documented as a localization signal but never implemented — country and
  language resolution used only IP and phone region.
- Hotel search during itinerary generation required a client-supplied IATA city code and silently
  produced no hotels at all if it was missing.
- Only activity items got images during itinerary generation; hotel items never did.
- The README's "What's still genuinely missing" section had drifted badly out of date (referencing
  work — Paystack scheduled jobs, version diffs, day segments, hotel replacement, flights — that
  had already been built in earlier sessions); rewritten to match the current code.

## Missing tables: usage tracking, durable caches, RLS

### Added
- `provider_usage` (per external-provider call, written by the resilience observer) and
  `api_usage` (per inbound request, written by middleware, sampled) tables + retention.
- `geocoding_cache` / `currency_cache`: durable Postgres fallback behind Redis for geocoding and
  currency lookups (`Redis → durable cache → live provider`), each with its own expiry;
  `CurrencyService` / `GeocodingService` updated to use them.
- Row-Level Security policies (migration `0021`) on trips (+ everything hanging off a trip) and
  the other directly user-owned tables — non-breaking by default (falls through when the session
  variable is unset); `RLS_ENFORCE` + `app/db/rls.py` (`set_rls_user`, wired into
  `get_current_user`) to actually turn it on.
- `GET /admin/provider-health` (§78, from the previous session) now has durable history behind it.

## Provider resilience

### Added
- `app/core/resilience.py`: `RetryPolicy` (exponential backoff + jitter, honours `Retry-After`,
  capped), `CircuitBreaker` (closed/open/half-open, one probe at a time), `call_resilient`.
- `app/core/redaction.py`: strips API keys/tokens from query strings, `Authorization` headers and
  known token shapes before anything is logged.
- `app/core/provider_health.py` + `GET /admin/provider-health` (`analytics:view`): live circuit
  state per provider.
- Wired into every HTTP-backed provider: weather, currency, geocoding, images, maps, Amadeus
  (separate breaker per resource: hotels/flights/activities), OpenAI chat/embeddings/Whisper,
  IPinfo, Paystack.
- New settings: `PROVIDER_RETRY_ATTEMPTS`, `PROVIDER_RETRY_BASE_DELAY_SECONDS`,
  `PROVIDER_RETRY_MAX_DELAY_SECONDS`, `CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_RECOVERY_SECONDS`.

### Fixed (security)
- **API keys were being logged.** WeatherAPI, OpenCage and CurrencyAPI pass their key as a query
  parameter, and `httpx`'s exception messages embed the full request URL; every provider that
  logged `str(exc)` was writing live API keys into the logs. All now redacted.
- A 404 from Amadeus (a normal "not found") was indistinguishable from the provider being down for
  circuit-breaker purposes; it is now explicitly excluded.
- Every provider previously waited out its FULL timeout on every failed call with no retry and no
  protection against a provider that is down hard; a slow provider could exhaust request latency
  and, unprotected, keep being hit at full volume during an outage.

## Background workers and scheduled maintenance

### Added
- **Queued email delivery** (`QueuedEmailProvider` → `email.deliver`): every email is asynchronous with no caller
  changes; the payload is parked in Redis and the task carries only an opaque key (no OTP in broker arguments/logs);
  retries with backoff for transient errors, no retry for permanent rejections; direct-send fallback if the broker is down.
- **Attachment extraction and knowledge-base ingestion as worker jobs** (`attachments.process`, `rag.ingest`) with
  status tracking (`pending/processing → completed|ready|failed`), idempotent and retry-aware; storage `download()`.
- **Celery beat** (new `beat` service): payment reconciliation, trial/subscription lifecycle notices and expiry sweep,
  data retention, nightly `daily_metrics` aggregation (+ `GET /admin/analytics/daily`), exchange-rate warming.
- Migration `0019`: lifecycle markers on trials/subscriptions, ingestion status on knowledge documents, `daily_metrics`.
- Auto-renewing subscriptions keep access through `SUBSCRIPTION_GRACE_HOURS` (a late renewal webhook no longer locks
  out a paying customer).

### Fixed (security)
- **OTP codes were stored in the clear in `email_logs`** (the subject contains the code); now redacted.
- **HTML injection in emails**: user-controlled names, trip titles and message bodies were interpolated unescaped —
  including into invitation emails sent to third parties. All escaped.
- **PDF export**: user text was interpreted as reportlab markup (crash on `<b>`, `<img src=…>` interpreted) and the raw
  trip title was used as a storage path. Escaped/sanitized; rendering moved off the event loop.
- Brevo 4xx rejections were treated like outages; they are now permanent (not retried).
- Subscriptions never left status `active` after their period ended; the sweep now expires them.

## Feature-flag enforcement

### Added
- **Every feature flag is now enforced server-side** where a feature exists (see the table in `README.md`); a test
  scans the router source so a new endpoint that forgets its gate, an accidentally-public endpoint, or an
  un-rate-limited provider endpoint fails the build. Previously only Discover, Companion messages and Voice were gated.
- **Global admin toggles**: a kill switch (503 `feature_unavailable`) and "open to all" per flag —
  `GET/PUT /admin/feature-flags` (new `flags:manage` permission), table `feature_flags` (migration `0018`), cached
  15 s in Redis and invalidated on change so it applies to every replica at once.
- Pure decision rules `kill switch > per-user override > open-to-all > plan`; 403 errors now carry a `reason`.
- **Per-tool entitlement in the Companion**: the agent is only offered tools the user is entitled to and
  `execute_tool` re-checks — the intent allowing a request no longer implies its read tools are allowed.
- **MEMORY** now gates remembering/recalling (Companion, planning, Discover); **PREMIUM_AI** selects the strong
  model tier for itinerary generation/revisions. Queued planning jobs re-check `PLANNER` when they run.
- Per-user rate limits on the provider-backed endpoints (hotels, flights, activities, maps, geocoding, images,
  weather, currency, knowledge search).

### Fixed
- The README still described `POST /entitlements/me/overrides` as a legitimate self-service tool, `subscribe` as
  free activation, and `/places` / `/rag/documents` as open — all removed or gated long ago; the docs now match the code.
- `LIVE_TRAVEL` is the only flag with nothing behind it (no live-travel feature exists yet).

## Dynamic RBAC

### Added
- **Database-driven RBAC**: `admin_permissions`, `admin_roles`, `admin_role_permissions`, `admin_user_roles`
  (migration `0017`, which also migrates each admin's old single role and drops that column). Custom roles,
  runtime permission edits, admins with several roles, `GET /admin/me|permissions|roles`, role CRUD, role
  assignment. See "RBAC" in `README.md`.
- Safety rails: last active Super Admin protected, no self-disable, system/super roles protected, assigned roles
  cannot be deleted, only catalog permissions grantable.
- **Denied attempts are audited** (they were not before); roster/role changes are audited with before/after.
- **Audit log immutability enforced by the database** (trigger rejects UPDATE/DELETE/TRUNCATE).
- `sync_rbac_catalog` (API start-up + `scripts.sync_rbac`) and `scripts.create_super_admin` for production bootstrap.

### Fixed
- `scripts/seed_dev_data.py` could not have worked with the current signup model (the seed user had no email,
  username, name, phone or password); it now creates a real, loginable Super Admin.

## Discover on LangGraph

### Added
- Discover runs as a LangGraph workflow (constraints → cache → currency → AI candidates → verification →
  ranking/validation → persistence) with injected ports; see "Discover" in `README.md`.
- Season-fit scoring, personalization from saved preferences (reported in `notes`), already-visited filtering
  applied after the shared cache, `exclude_visited`, per-result `score`/`season_fit`.

### Fixed
- Budget semantics: the prompt said "per traveler" but scoring used the group total; the budget is now the
  TOTAL for the group everywhere.
- **Wrong-currency fallback**: when conversion failed, USD costs were compared with a budget in another currency
  (e.g. naira). Now a single real rate is required, or the search fails honestly.
- Candidates with a missing/zero cost estimate scored as "free" and could win; they are now dropped. Candidates
  whose geocoded country contradicts the model's are dropped.
- Cache key ignored travel dates; weather described "the next N days" instead of the trip's dates; candidates
  were verified one after another (now concurrent and bounded); candidate JSON in code fences failed to parse;
  module-level LLM/activity clients replaced by injected ports.
- `trip_planning_cta.budget_amount` was the estimated cost; it is now the user's budget (`estimated_cost` added).

## Companion on LangGraph, intent routing, conversational revisions

### Added
- **Companion turn as a LangGraph workflow** with intent routing (12 intents, rule-based + fast-model
  fallback), per-intent feature gating, per-intent tool allow-lists, and relevance-based context
  (trip / preferences / memories loaded only when useful). See "Companion" in `README.md`.
- **Conversational itinerary revision**: `propose_itinerary_revision` → validated, diffed, PENDING
  proposal; confirmation applies it atomically with a version snapshot and is refused if the trip
  changed since (`REVISE_TRIP`, migration `0016`).
- New tools: `propose_itinerary_revision`, `create_trip_version`, `calculate_route`, `search_destinations`.
- **Cost-aware model routing** (`tier="fast"|"strong"`, `AI_FAST_MODEL`/`AI_STRONG_MODEL`), configurable
  request timeouts (`AI_REQUEST_TIMEOUT_SECONDS`, `AI_PLANNING_TIMEOUT_SECONDS`).

### Fixed
- **Itinerary generation would have timed out.** The OpenAI client had a hard 30 s timeout, far too short
  for a multi-thousand-token itinerary; planning now uses a 150 s timeout on the strong tier.
- Provider-sourced itinerary items stored the model's short catalogue key ("h1") as `external_id`; they now
  store the provider's real id.
- The Companion no longer uses a module-level LLM client (dependency-injected, mockable).
- Memory extraction and summarization now run on the cheap model tier.

## AI itinerary generation (LangGraph)

### Added
- `POST /trips/{id}/generate` (+ job polling, trip preferences endpoints) — see "Itinerary generation"
  in `README.md`. LangGraph workflow: geocode → gather (hotels ∥ activities ∥ weather) → currency →
  draft → ground → validate → repair loop → persist. Runs in a Celery task in production, inline in dev;
  Idempotency-Key, per-trip lock, PLANNER feature flag, rate limit.
- Pure planning rules (validation + deterministic repair), strict LLM output parsing, workflow spec with
  a LangGraph builder and an in-repo reference runner used by tests.
- Migration `0015`: `trips.overview`, provenance columns on `trip_items` (`source`, `external_id`,
  `booking_link`, `image_url`), `trip_preferences`.
- `langgraph>=0.2.50,<2.0` in `requirements.txt`.

### Fixed
- **Version history was incomplete.** `parent_version_id` was always null and snapshots dropped
  description, provider, notes, etc., so restore lost data. Versions now chain to their parent and
  snapshots (and restore) are complete.
- Manually added items are now tagged `source=manual`.

## Paystack payments

### Added
- **Checkout, verification, webhooks, renewals, cancellation** — see "Payments (Paystack)" in `README.md`.
  Endpoints: `POST /payments/checkout`, `POST /payments/verify`, `GET /payments/me`,
  `POST /payments/webhook/paystack`, `PATCH /plans/{id}`, `GET /admin/payments`.
- Provider abstraction `PaymentProvider` with `PaystackProvider` and `MockPaymentProvider`.
- Tables (migration `0014`): `payments`, `subscription_events`, `payment_webhook_events`;
  `plans.paystack_plan_code`; `subscriptions.auto_renew` and Paystack customer/subscription codes.
- `payments:view` admin permission (ADMIN, FINANCE_ADMIN); payments included in the user data export.

### Fixed
- **Subscriptions never expired.** `get_active_subscription` ignored `current_period_end`, so a paid
  one-month plan stayed active forever. It now requires the period to be in the future.
- Cancelling a paid plan no longer cuts access immediately: renewal stops, access lasts to period end.

## Hardening & auth completion (this delivery)

### Security fixes
- **Free premium access closed.** Removed `POST /entitlements/me/overrides` (any user could enable any
  feature flag on their own account). Overrides are now admin-only:
  `PUT /admin/users/{user_id}/feature-overrides` (`plans:manage`, audit-logged).
- **No free paid plans.** `POST /subscriptions/subscribe` only accepts free plans; paid plans return
  `402`. `SubscriptionService.activate_paid_subscription` is reserved for the verified-payment path.
- **OTP brute force closed.** The failed-attempt counter is committed before the error is raised (it was
  rolled back before, so the limit never triggered); codes are burned at the limit; `verify-email` and
  `reset-password` are rate-limited per IP and per account; OTPs must be digits; resend cooldown enforced.
- **Trip PDF upload authorization.** `POST /uploads/trip-pdf/{trip_id}` requires owner/editor access;
  client filenames can no longer choose or overwrite storage paths (sanitized + random prefix, no upsert).
- **Uploads hardened.** Streaming size limit, real content type from magic bytes (client header ignored),
  macro-enabled Word files refused, optional ClamAV scan (fail-closed option), body-size ceiling middleware.
- **Spoof-proof client IP.** `X-Forwarded-For` is only honoured for `TRUSTED_PROXY_HOPS` proxies.
- **Revocation actually revokes.** Refresh tokens now honour `sessions_invalidated_at` (admin "revoke
  sessions" previously did nothing because users could keep refreshing). Refresh tokens rotate and re-use
  is detected and revoked.
- **Password rules unified**; reset now enforces them and signs out all sessions; 72-byte bcrypt limit.
- **Unsafe defaults removed.** `DEBUG` defaults to false; staging/production refuse weak `SECRET_KEY`,
  wildcard/empty CORS, or `DEBUG=true`; CORS never falls back to `*` with credentials.
- **Validation errors no longer echo submitted values** (they included passwords) and every FastAPI
  validation/HTTP error now uses the standard error envelope.

### Correctness fixes
- **ORM/migration enum mismatch (critical).** All 27 enum columns persisted member *names* while the
  migrations created PostgreSQL enums labelled with lowercase *values*; against a real database every
  read/write of an enum column would fail. Columns now persist values (`values_callable`).
- Avatar uploads are saved to `users.avatar_url`; attachment URLs are minted fresh on every read (the
  stored signed URL expired after an hour); Supabase calls no longer block the event loop.
- Race-safe signup (unique-constraint fallback returns 409 instead of 500).

### New features
- Google sign-in (`/auth/google`, `/auth/google/complete`), server-side ID-token verification.
- Rotating refresh tokens + per-device sessions (`user_sessions`), `logout`, `logout-all`,
  `change-password`, session list/revoke, `username-available`.
- `PATCH /users/me`, optional/skippable preferences (`user_preferences`), onboarding completion,
  avatar removal.
- User data export and OTP-confirmed account deletion (anonymize + purge).
- Launch-mode trial starts automatically at account activation.
- Provider factories/DI for email; Google and malware-scanner providers with mocks.

### Database
- Migration `0013_sessions_preferences`: `user_sessions`, `user_preferences`, `account_deletion` OTP
  purpose, `users(auth_provider, provider_subject_id)` index.

### Not changed / still to do
See "What's still genuinely missing" in `README.md`.
