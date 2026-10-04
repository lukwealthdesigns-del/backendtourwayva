"""
Import every ORM model here so Alembic's `target_metadata` (see
app/db/migrations/env.py) can discover them for autogeneration.

As later phases add models (trips, hotels, conversations, etc.),
import them here too.
"""
from app.db.models.admin import (  # noqa: F401
    AdminAuditLog,
    AdminMessage,
    AdminPermission,
    AdminRoleModel,
    AdminRolePermission,
    AdminUser,
    AdminUserRole,
    BroadcastJob,
)
from app.db.models.analytics import (  # noqa: F401
    AIUsageRecord,
    AnalyticsEvent,
    ApiUsageRecord,
    BookingClick,
    DailyMetric,
    ProviderUsageRecord,
)
from app.db.models.attachment import Attachment  # noqa: F401
from app.db.models.collaboration import PendingChangeVote, TripComment, TripInvitation  # noqa: F401
from app.db.models.conversation import Conversation, Message  # noqa: F401
from app.db.models.discover import DiscoveryResult, DiscoverySearch  # noqa: F401
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument  # noqa: F401
from app.db.models.memory import UserMemory  # noqa: F401
from app.db.models.monetization import (  # noqa: F401
    FeatureFlagOverride,
    FeatureFlagSetting,
    Plan,
    Subscription,
    TrialConfig,
    UserTrial,
)
from app.db.models.notification import EmailLog, Notification, NotificationPreference  # noqa: F401
from app.db.models.otp import OTPCode  # noqa: F401
from app.db.models.pending_change import PendingItineraryChange  # noqa: F401
from app.db.models.provider_cache import CurrencyRateCacheEntry, GeocodeCacheEntry  # noqa: F401
from app.db.models.payment import Payment, PaymentWebhookEvent, SubscriptionEvent  # noqa: F401
from app.db.models.place import Place  # noqa: F401
from app.db.models.saved_place import SavedPlace  # noqa: F401
from app.db.models.preferences import UserPreferences  # noqa: F401
from app.db.models.security import BlockedIP, LoginAttempt, SecurityEvent  # noqa: F401
from app.db.models.session import UserSession  # noqa: F401
from app.db.models.trip import (  # noqa: F401
    Trip,
    TripCost,
    TripDay,
    TripItem,
    TripMember,
    TripNote,
    TripPreferences,
    TripRoute,
    TripVersion,
)
from app.db.models.travel_history import VisitedDestination, VisitedPlace  # noqa: F401
from app.db.models.user import User  # noqa: F401

__all__ = [
    "User", "OTPCode", "Trip", "TripMember", "TripDay", "TripItem", "TripVersion", "Place",
    "UserMemory", "Conversation", "Message", "KnowledgeDocument", "KnowledgeChunk", "PendingItineraryChange",
    "TripInvitation", "TripComment", "PendingChangeVote",
    "Plan", "Subscription", "TrialConfig", "UserTrial", "FeatureFlagOverride",
    "AdminUser", "AdminAuditLog", "AdminMessage", "BroadcastJob",
    "LoginAttempt", "BlockedIP", "SecurityEvent",
    "Notification", "NotificationPreference", "EmailLog",
    "AIUsageRecord", "AnalyticsEvent",
    "TripNote", "TripCost", "TripRoute",
    "Attachment", "VisitedDestination", "VisitedPlace",
    "DiscoverySearch", "DiscoveryResult",
]
