"""Application-wide constants and enums shared across modules."""
from __future__ import annotations

from enum import Enum


class UserStatus(str, Enum):
    PENDING = "pending"          # created, awaiting OTP verification
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class AuthProvider(str, Enum):
    EMAIL = "email"
    GOOGLE = "google"


class OTPPurpose(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
    PHONE_VERIFICATION = "phone_verification"
    LOGIN_2FA = "login_2fa"
    ACCOUNT_DELETION = "account_deletion"


class UploadUseCase(str, Enum):
    """Drives which storage provider handles a given upload —
    see app/providers/storage/factory.py."""

    USER_AVATAR = "user_avatar"
    DESTINATION_IMAGE = "destination_image"
    ATTACHMENT = "attachment"
    TRIP_PDF = "trip_pdf"
    TRAVEL_DOCUMENT = "travel_document"


class StorageProviderName(str, Enum):
    CLOUDINARY = "cloudinary"
    SUPABASE_STORAGE = "supabase_storage"


class UserRole(str, Enum):
    USER = "user"
    PREMIUM_USER = "premium_user"
    SUPPORT = "support"
    MODERATOR = "moderator"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class TripStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TripMemberRole(str, Enum):
    """Per Master Blueprint §43 (Trip Collaboration). Only OWNER is
    assignable in this Phase 3 delivery (creator becomes owner
    automatically) — invitations for editor/contributor/viewer land
    in Phase 5 (Collaboration)."""

    OWNER = "owner"
    EDITOR = "editor"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class TripItemType(str, Enum):
    HOTEL = "hotel"
    FLIGHT = "flight"
    ACTIVITY = "activity"
    RESTAURANT = "restaurant"
    ATTRACTION = "attraction"
    TRANSPORT = "transport"
    NOTE = "note"
    CUSTOM = "custom"


class PlaceCategory(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    LANDMARK = "landmark"
    MUSEUM = "museum"
    PARK = "park"
    SHOPPING = "shopping"
    NIGHTLIFE = "nightlife"
    OTHER = "other"


class MemorySource(str, Enum):
    USER_STATED = "user_stated"
    COMPANION_EXTRACTED = "companion_extracted"
    SYSTEM = "system"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class PendingChangeAction(str, Enum):
    ADD_ITEM = "add_item"
    UPDATE_ITEM = "update_item"
    DELETE_ITEM = "delete_item"
    # A whole-itinerary revision produced from a natural-language request
    # ("make it cheaper", "remove day 3"). Applied only after a human confirms.
    REVISE_TRIP = "revise_trip"


class PendingChangeStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BillingInterval(str, Enum):
    FREE = "free"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentStatus(str, Enum):
    PENDING = "pending"        # checkout created, awaiting the provider's confirmation
    SUCCESS = "success"        # charge verified with the provider (amount + currency match)
    FAILED = "failed"          # provider reported failure, or the verified amount did not match
    ABANDONED = "abandoned"    # customer left checkout without paying
    REFUNDED = "refunded"      # fully refunded (a PARTIAL refund keeps SUCCESS; see Payment.refunded_amount_minor)
    DISPUTED = "disputed"      # customer opened a chargeback/dispute with their bank


class FeatureFlag(str, Enum):
    """Master Blueprint §50 — the exact flag list specified there."""

    DISCOVER = "DISCOVER"
    PLANNER = "PLANNER"
    COMPANION = "COMPANION"
    VOICE = "VOICE"
    ATTACHMENTS = "ATTACHMENTS"
    MEMORY = "MEMORY"
    HOTELS = "HOTELS"
    FLIGHTS = "FLIGHTS"
    ACTIVITIES = "ACTIVITIES"
    WEATHER = "WEATHER"
    LIVE_TRAVEL = "LIVE_TRAVEL"
    PDF_EXPORT = "PDF_EXPORT"
    COLLABORATION = "COLLABORATION"
    PREMIUM_AI = "PREMIUM_AI"


# Flags available even with no plan, no subscription, and no trial —
# the floor every signed-up user gets. Deliberately small: enough to
# use Discover/Planning/weather lookups without paying, but Companion,
# hotels/flights/activities search, and premium AI require a plan or
# trial. Tune this list as the product's actual free tier is decided.
FREE_TIER_DEFAULT_FLAGS: frozenset[FeatureFlag] = frozenset(
    {FeatureFlag.DISCOVER, FeatureFlag.PLANNER, FeatureFlag.WEATHER}
)


class AdminRole(str, Enum):
    """Master Blueprint §55 — the exact role list specified there."""

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPPORT_ADMIN = "support_admin"
    CONTENT_ADMIN = "content_admin"
    FINANCE_ADMIN = "finance_admin"
    ANALYTICS_ADMIN = "analytics_admin"
    MODERATION_ADMIN = "moderation_admin"


class AdminMessageChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    BOTH = "both"


class AdminMessageStatus(str, Enum):
    SENT = "sent"
    FAILED = "failed"


class BroadcastSegment(str, Enum):
    ALL = "all"
    FREE = "free"
    TRIAL = "trial"
    PREMIUM = "premium"
    INACTIVE = "inactive"


class BroadcastStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class SecurityEventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationType(str, Enum):
    TRIP_INVITATION = "trip_invitation"
    ITINERARY_CHANGE_PROPOSED = "itinerary_change_proposed"
    ITINERARY_CHANGE_DECIDED = "itinerary_change_decided"
    TRIP_COMMENT = "trip_comment"
    ADMIN_MESSAGE = "admin_message"
    TRIAL_EXPIRING = "trial_expiring"
    SUBSCRIPTION_EVENT = "subscription_event"
    SECURITY_ALERT = "security_alert"
    SYSTEM_ANNOUNCEMENT = "system_announcement"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    BOTH = "both"


class AttachmentExtractionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class AttachmentCategory(str, Enum):
    """Which Supabase Storage bucket an `attachments` row lives in. Values
    match UploadUseCase 1:1 (see AttachmentService.use_case_for), so a stored
    row can always be routed back to the right bucket — previously every row
    was signed against the attachments bucket, which was wrong for generated
    trip PDFs. Every upload path now writes a row (closing the gap where
    /uploads/attachment and /uploads/travel-document bypassed the table)."""

    ATTACHMENT = "attachment"
    TRAVEL_DOCUMENT = "travel_document"
    TRIP_PDF = "trip_pdf"          # server-generated itinerary PDFs; never user-uploaded


WAYVA_ID_PREFIX = "WAYVA"
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 20
