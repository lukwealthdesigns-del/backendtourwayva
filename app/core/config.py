"""
Centralized application configuration.

All environment-specific values MUST come from here — never hard-code
credentials, URLs, or business-rule constants elsewhere in the codebase.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- App ---
    APP_NAME: str = "Tour-Wayva"
    ENVIRONMENT: str = "development"  # development | testing | staging | production
    DEBUG: bool = False  # never default to debug: SQL echo / verbose errors must be opt-in
    API_V1_PREFIX: str = "/api/v1"
    SECRET_KEY: str
    BACKEND_CORS_ORIGINS: List[str] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    # Number of reverse proxies / load balancers WE operate in front of
    # the app. 0 = trust nothing: use the socket peer address and ignore
    # X-Forwarded-For (which any client can forge). See app/utils/net.py.
    TRUSTED_PROXY_HOPS: int = 0

    # --- JWT / sessions ---
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    MAX_ACTIVE_SESSIONS_PER_USER: int = 10
    # A rotated refresh token presented again within this window is treated
    # as a benign client retry (401, session kept). After it, re-use is
    # treated as token theft and the session is revoked.
    REFRESH_REUSE_GRACE_SECONDS: int = 10

    # --- Database ---
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # --- Supabase ---
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_STORAGE_ATTACHMENTS_BUCKET: str = "attachments"
    SUPABASE_STORAGE_TRIP_PDFS_BUCKET: str = "trip-pdfs"
    SUPABASE_STORAGE_TRAVEL_DOCS_BUCKET: str = "travel-documents"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Cloudinary ---
    # Public URL of the web app (e.g. https://app.example.com). Used for the button in the welcome email; if empty the
    # email is sent without a button.
    FRONTEND_URL: str = ""
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""
    CLOUDINARY_AVATAR_FOLDER: str = "tourwayva/avatars"
    CLOUDINARY_DESTINATION_FOLDER: str = "tourwayva/destinations"

    # --- Brevo ---
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "no-reply@tourwayva.com"
    BREVO_SENDER_NAME: str = "Tour-Wayva"

    # --- Paystack (payments) ---
    # Secret key: server-side only, never sent to a client. The public key is
    # returned by POST /payments/checkout so the frontend can use Paystack's
    # inline/popup checkout if it wants to; the redirect flow needs neither.
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_BASE_URL: str = "https://api.paystack.co"
    PAYSTACK_TIMEOUT_SECONDS: int = 20
    # Where Paystack sends the customer after checkout (your frontend page,
    # which then calls POST /payments/verify with the reference).
    PAYSTACK_CALLBACK_URL: str = ""
    # Currencies enabled on YOUR Paystack account. All use 2 decimal places,
    # so amounts are converted to minor units (x100) — see app/utils/money.py.
    PAYSTACK_SUPPORTED_CURRENCIES: List[str] = ["NGN", "GHS", "ZAR", "KES", "USD"]
    # Source IPs Paystack publishes for webhook delivery. The HMAC signature
    # stays the authoritative check; this is defense in depth. Empty list =
    # IP check disabled. Paystack can change these — re-check their docs.
    PAYSTACK_WEBHOOK_IP_ALLOWLIST: List[str] = ["52.31.139.75", "52.49.173.169", "52.214.14.220"]
    # False (default): a validly-signed webhook from an unlisted IP is still
    # processed but logged + recorded as a security event. True: it is
    # rejected with 403. Only enable after TRUSTED_PROXY_HOPS is correct for
    # your deployment, or every webhook will look like it came from your proxy.
    PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST: bool = False

    @property
    def paystack_configured(self) -> bool:
        return bool(self.PAYSTACK_SECRET_KEY)

    # --- Analytics / cost dashboard (Master Blueprint §57-58, §89) ---
    # Estimated commission rate applied to a booking-link CLICK's displayed
    # price, by item_type, to produce an "estimated affiliate revenue" figure
    # for the admin dashboard. Tour-Wayva has no actual booking/payout
    # relationship with Amadeus, so this is always an estimate from a
    # configured rate, never a confirmed payout (Blueprint §108).
    ANALYTICS_HOTEL_COMMISSION_RATE: float = 0.04
    ANALYTICS_FLIGHT_COMMISSION_RATE: float = 0.01
    ANALYTICS_ACTIVITY_COMMISSION_RATE: float = 0.08
    # Trailing window used for churn-rate / MRR "new vs lost" comparisons.
    ANALYTICS_CHURN_WINDOW_DAYS: int = 30

    # --- Itinerary generation (long AI workflow, Master Prompt §69) ---
    # "background": POST /trips/{id}/generate returns 202 immediately and a Celery
    # worker runs the workflow; poll GET /trips/{id}/generate/{job_id}.
    # "inline": run inside the request (development/tests only — it can take a minute).
    # Unset => background in staging/production, inline elsewhere.
    PLANNING_EXECUTION: Optional[str] = None
    PLANNING_JOB_TTL_SECONDS: int = 60 * 60 * 24
    PLANNING_LOCK_TTL_SECONDS: int = 60 * 10
    # A generated itinerary is shown as "upcoming" when the trip starts within this many days,
    # and as "ready" while it is further away (see app/modules/trips/display_status.py).
    TRIP_UPCOMING_WINDOW_DAYS: int = 30

    @property
    def planning_execution(self) -> str:
        if self.PLANNING_EXECUTION in ("inline", "background"):
            return self.PLANNING_EXECUTION
        return "background" if self.ENVIRONMENT in ("staging", "production") else "inline"

    # --- Row-Level Security (Master Prompt §5, §64) ---
    # Off by default: turning this on is meaningful only once the app connects as a
    # non-superuser Postgres role (migration 0021's policies are non-breaking either way).
    RLS_ENFORCE: bool = False

    # --- Localization (Master Prompt §8) ---
    IPINFO_CACHE_TTL_SECONDS: int = 60 * 60 * 24  # 24h: country-from-IP is stable enough to cache a day
    SUSPICIOUS_LOGIN_DETECTION: bool = True

    # --- API usage tracking (Master Prompt §5 `api_usage`) ---
    API_USAGE_SAMPLE_RATE: float = 1.0     # 1.0 = log every request; lower this in high-traffic production

    # --- Provider resilience (Master Prompt §78) ---
    PROVIDER_RETRY_ATTEMPTS: int = 3
    PROVIDER_RETRY_BASE_DELAY_SECONDS: float = 0.4
    PROVIDER_RETRY_MAX_DELAY_SECONDS: float = 5.0
    CIRCUIT_FAILURE_THRESHOLD: int = 5      # consecutive transient failures that open a provider's circuit
    CIRCUIT_RECOVERY_SECONDS: float = 30.0  # how long it stays open before one probe call is allowed

    # --- Background work (Master Prompt §69) ---
    # "background": emails, attachment extraction and knowledge-base ingestion are handed to Celery
    # workers; "inline": they run inside the request (development/tests). Unset => background in
    # staging/production, inline elsewhere. (Itinerary generation has its own PLANNING_EXECUTION.)
    TASKS_EXECUTION: Optional[str] = None
    # If the broker is unreachable when an email is queued, send it directly instead of losing it.
    EMAIL_QUEUE_FALLBACK_INLINE: bool = True
    EMAIL_PAYLOAD_TTL_SECONDS: int = 1800

    @property
    def tasks_execution(self) -> str:
        if self.TASKS_EXECUTION in ("inline", "background"):
            return self.TASKS_EXECUTION
        return "background" if self.ENVIRONMENT in ("staging", "production") else "inline"

    # --- Scheduled maintenance (Celery beat) ---
    TRIAL_REMINDER_DAYS: int = 3
    SUBSCRIPTION_REMINDER_DAYS: int = 3
    # Auto-renewing subscriptions keep access this long past their period end, so a
    # renewal webhook that is a little late does not lock a paying customer out.
    SUBSCRIPTION_GRACE_HOURS: int = 24
    PAYMENT_RECONCILE_MIN_AGE_MINUTES: int = 10
    PAYMENT_RECONCILE_MAX_AGE_HOURS: int = 48
    # Data retention (Master Prompt §9, §77): personal/operational records do not live forever.
    OTP_RETENTION_HOURS: int = 48
    SESSION_RETENTION_DAYS: int = 30
    SECURITY_LOG_RETENTION_DAYS: int = 90          # login attempts incl. IP addresses
    EMAIL_LOG_RETENTION_DAYS: int = 90
    NOTIFICATION_RETENTION_DAYS: int = 180         # READ notifications only
    WEBHOOK_EVENT_RETENTION_DAYS: int = 90
    PROVIDER_USAGE_RETENTION_DAYS: int = 30
    API_USAGE_RETENTION_DAYS: int = 30
    CURRENCY_CACHE_RETENTION_DAYS: int = 7  # historical rows only; current rate is always the latest one
    # Exchange-rate pairs (BASE:TARGET) kept warm in the cache.
    CURRENCY_WARM_PAIRS: List[str] = ["USD:NGN", "USD:EUR", "USD:GBP", "USD:GHS", "USD:KES", "USD:ZAR", "EUR:NGN", "GBP:NGN"]

    # --- OTP ---
    OTP_LENGTH: int = 6
    OTP_EXPIRY_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_SIGNUP_TOKEN_EXPIRE_MINUTES: int = 15

    # --- AI (Phase 4, scaffolded) ---
    OPENAI_API_KEY: str = ""
    AI_PRIMARY_MODEL: str = "gpt-4.1"
    AI_FALLBACK_MODEL: str = "gpt-4.1-mini"
    # Cost-aware routing (Master Prompt §97): cheap/fast model for classification,
    # summarization and memory extraction; strong model for itinerary planning.
    # Unset => the primary model is used for that tier.
    AI_FAST_MODEL: Optional[str] = None
    AI_STRONG_MODEL: Optional[str] = None
    AI_REQUEST_TIMEOUT_SECONDS: int = 30
    # Itinerary generation writes thousands of tokens; 30s is not enough for a strong model.
    AI_PLANNING_TIMEOUT_SECONDS: int = 150
    AI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # --- External travel/data providers (Phase 2, scaffolded) ---
    AMADEUS_CLIENT_ID: str = ""
    AMADEUS_CLIENT_SECRET: str = ""
    AMADEUS_ENV: str = "test"
    WEATHER_API_KEY: str = ""
    OPENCAGE_API_KEY: str = ""
    CURRENCY_API_KEY: str = ""
    IPINFO_TOKEN: str = ""
    UNSPLASH_ACCESS_KEY: str = ""

    # Opening-hours verification (planning). Amadeus Tours & Activities returns no structured opening
    # hours, so this uses OpenStreetMap's `opening_hours` tag via the Overpass API — keyless and
    # free, but community-maintained and best-effort, so a mismatch is only ever a WARNING.
    # "overpass" | "none". Point OVERPASS_API_URL at your own instance for real traffic: the public
    # servers are a shared volunteer resource.
    OPENING_HOURS_PROVIDER: str = "overpass"
    OVERPASS_API_URL: str = "https://overpass-api.de/api/interpreter"
    OPENING_HOURS_TIMEOUT_SECONDS: int = 8
    OPENING_HOURS_SEARCH_RADIUS_M: int = 75
    OPENING_HOURS_MAX_LOOKUPS_PER_PLAN: int = 15
    # Ceiling for the whole batch of lookups in one planning run, so a slow Overpass never
    # delays itinerary generation by more than this.
    OPENING_HOURS_TOTAL_TIMEOUT_SECONDS: int = 20
    ORS_API_KEY: str = ""  # OpenRouteService — maps/routing provider

    # --- Uploads ---
    MAX_AVATAR_SIZE_MB: int = 5
    MAX_ATTACHMENT_SIZE_MB: int = 20
    # Hard ceiling on ANY request body, checked from Content-Length before the
    # body is parsed (multipart overhead included).
    MAX_REQUEST_BODY_MB: int = 30
    ALLOWED_ATTACHMENT_MIME_TYPES: List[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ]

    # Optional ClamAV (clamd) malware scanning of uploads. Leave CLAMAV_HOST
    # empty to disable. With REQUIRE_MALWARE_SCAN=true, uploads are REJECTED
    # whenever the scanner is unconfigured or unreachable (fail closed).
    CLAMAV_HOST: str = ""
    CLAMAV_PORT: int = 3310
    CLAMAV_TIMEOUT_SECONDS: int = 20
    REQUIRE_MALWARE_SCAN: bool = False

    @field_validator("ALLOWED_ATTACHMENT_MIME_TYPES", mode="before")
    @classmethod
    def _split_mimes(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _validate_environment_guards(self) -> "Settings":
        allowed_envs = {"development", "testing", "staging", "production"}
        if self.ENVIRONMENT not in allowed_envs:
            raise ValueError(f"ENVIRONMENT must be one of {sorted(allowed_envs)}.")

        if self.ENVIRONMENT in ("staging", "production"):
            weak_prefixes = ("change-me", "changeme", "secret", "test", "dev")
            if len(self.SECRET_KEY) < 32 or self.SECRET_KEY.lower().startswith(weak_prefixes):
                raise ValueError(
                    "SECRET_KEY must be a random value of at least 32 characters "
                    "in staging/production."
                )
            if not self.BACKEND_CORS_ORIGINS or "*" in self.BACKEND_CORS_ORIGINS:
                raise ValueError(
                    "BACKEND_CORS_ORIGINS must list explicit origins (no wildcard, not empty) "
                    "in staging/production."
                )
            if self.DEBUG:
                raise ValueError("DEBUG must be false in staging/production.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — import this everywhere instead of
    instantiating Settings() directly."""
    return Settings()


settings = get_settings()
