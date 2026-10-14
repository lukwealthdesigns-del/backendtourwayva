"""
GuestService: guest sessions, their limits, claiming guest work into a real account, and cleanup.

Design: a guest is a real `users` row (is_guest=True) created for one session, so every existing service, ownership check
and row-level-security policy applies to guest data unchanged. The browser holds an opaque token (only its hash is stored).
Claiming re-points the guest user's trips, conversations and searches at the real account in ONE transaction, under a row
lock, so it is all-or-nothing and safe to retry or to fire twice at once.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import AuthProvider, UserRole, UserStatus
from app.core.exceptions import ForbiddenError, GuestLimitError, GuestSessionExpiredError
from app.core.logging import get_logger
from app.db.models.conversation import Conversation
from app.db.models.discover import DiscoverySearch
from app.db.models.guest import GuestSession
from app.db.models.trip import Trip, TripCost, TripMember, TripNote, TripVersion
from app.db.models.user import User
from app.modules.guest import rules
from app.modules.guest.schemas import ClaimedTrip, ClaimResponse, GuestLimits, GuestSessionResponse, GuestUsage
from app.utils.otp_generator import generate_wayva_id

logger = get_logger(__name__)
_CLAIMED_USER_RETENTION_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def limits() -> GuestLimits:
    return GuestLimits(
        companion_messages=settings.GUEST_COMPANION_MESSAGES, generations=settings.GUEST_GENERATIONS,
        max_trips=settings.GUEST_MAX_TRIPS, max_trip_days=settings.GUEST_MAX_TRIP_DAYS, session_ttl_hours=settings.GUEST_SESSION_TTL_HOURS,
    )


class GuestService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    async def create_session(self, *, client_ip: Optional[str], language: Optional[str], currency: Optional[str], country: Optional[str]) -> GuestSessionResponse:
        if not settings.GUEST_MODE_ENABLED:
            raise ForbiddenError("Guest access is turned off. Please create an account to continue.")
        now = _now()
        iph = rules.hash_ip(client_ip, settings.SECRET_KEY) if client_ip else None
        if iph:
            opened = (await self.db.execute(
                select(func.count()).select_from(GuestSession).where(GuestSession.ip_hash == iph, GuestSession.created_at >= now - timedelta(days=1))
            )).scalar_one()
            if opened >= settings.GUEST_SESSIONS_PER_IP_PER_DAY:
                raise GuestLimitError(
                    "Too many guest sessions were started from this network today. Create a free account to keep going.",
                    {"reason": "ip_cap"},
                )

        ident = uuid.uuid4().hex
        user = User(
            wayva_id=generate_wayva_id(), username=f"g{ident[:12]}", first_name="Guest", last_name="Traveler",
            email=f"guest-{ident}@guest.invalid", phone_number_e164=f"+999{secrets.randbelow(10 ** 12):012d}",
            phone_country_code="999", phone_region_code="ZZ", password_hash=None, auth_provider=AuthProvider.EMAIL,
            status=UserStatus.ACTIVE, is_active=True, role=UserRole.USER, is_guest=True,
            language=(language or "")[:10] or None, currency=(currency or "").upper() if currency and len(currency) == 3 else None,
            country=(country or "").upper() if country and len(country) == 2 else None,
        )
        self.db.add(user)
        await self.db.flush()
        token = rules.new_token()
        session = GuestSession(
            token_hash=rules.hash_token(token), guest_user_id=user.id, status="active", last_activity_at=now,
            expires_at=rules.new_expiry(now, settings.GUEST_SESSION_TTL_HOURS), ip_hash=iph,
        )
        self.db.add(session)
        await self.db.commit()
        logger.info("guest_session_created", session_id=str(session.id))
        return GuestSessionResponse(token=token, expires_at=session.expires_at, limits=limits(), usage=GuestUsage(companion_messages_used=0, generations_used=0, trips=0))

    async def _session_by_token(self, token: Optional[str], *, lock: bool = False) -> Optional[GuestSession]:
        if not rules.looks_like_token(token):
            return None
        stmt = select(GuestSession).where(GuestSession.token_hash == rules.hash_token(token))
        if lock:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def authenticate(self, token: Optional[str]) -> tuple[GuestSession, User]:
        """The guest behind a token, or GuestSessionExpiredError. Every use pushes the expiry out (sliding window)."""
        session = await self._session_by_token(token)
        now = _now()
        if session is None or session.status != "active" or session.expires_at <= now:
            raise GuestSessionExpiredError("Your guest session has ended. Start a new one, or create an account to keep your trips.")
        user = await self.db.get(User, session.guest_user_id)
        if user is None or not user.is_guest or not user.is_active:
            raise GuestSessionExpiredError("Your guest session has ended. Start a new one, or create an account to keep your trips.")
        if rules.should_touch(session.last_activity_at, now):
            session.last_activity_at = now
            session.expires_at = rules.new_expiry(now, settings.GUEST_SESSION_TTL_HOURS)
            await self.db.commit()                       # before the request sets its row-level-security user
        return session, user

    async def update_preferences(self, *, token: Optional[str], language: Optional[str], currency: Optional[str], country: Optional[str]) -> GuestSessionResponse:
        """A guest's language, currency and country live on their guest user, so replies and examples follow them. No account is needed
        (and no account endpoint is touched): this is the guest-session version of 'change my language'."""
        session, user = await self.authenticate(token)
        if language:
            user.language = language[:10]
        if currency and len(currency) == 3 and currency.isalpha():
            user.currency = currency.upper()
        if country and len(country) == 2 and country.isalpha():
            user.country = country.upper()
        await self.db.commit()
        return await self.status(session)

    async def usage(self, session: GuestSession) -> GuestUsage:
        trips = (await self.db.execute(select(func.count()).select_from(Trip).where(Trip.owner_id == session.guest_user_id))).scalar_one()
        from app.modules.planning.policy import PlanningPolicyService

        generations = await PlanningPolicyService(self.db).generations_this_month(session.guest_user_id)
        return GuestUsage(companion_messages_used=session.companion_messages_used, generations_used=generations, trips=int(trips))

    async def status(self, session: GuestSession) -> GuestSessionResponse:
        return GuestSessionResponse(expires_at=session.expires_at, limits=limits(), usage=await self.usage(session))

    # ------------------------------------------------------------------
    # Limits (enforced here, never in the app)
    # ------------------------------------------------------------------
    async def consume_companion_message(self, user_id: uuid.UUID) -> None:
        """Atomically spend one Companion message, or raise GuestLimitError. The increment commits with the caller's next
        commit (the Companion saves the user's message right after), so a failed request is not charged."""
        cap = settings.GUEST_COMPANION_MESSAGES
        result = await self.db.execute(
            update(GuestSession)
            .where(GuestSession.guest_user_id == user_id, GuestSession.companion_messages_used < cap)
            .values(companion_messages_used=GuestSession.companion_messages_used + 1)
            .returning(GuestSession.companion_messages_used)
        )
        if result.first() is None:
            raise GuestLimitError(
                "You've used all the free Companion messages in this guest session. Create a free account to keep chatting.",
                {"feature": "companion", "limit": cap, "used": cap},
            )

    async def enforce_trip_limit(self, user_id: uuid.UUID) -> None:
        count = (await self.db.execute(select(func.count()).select_from(Trip).where(Trip.owner_id == user_id))).scalar_one()
        if count >= settings.GUEST_MAX_TRIPS:
            raise GuestLimitError(
                f"Guests can plan up to {settings.GUEST_MAX_TRIPS} trips. Create a free account to save these and plan more.",
                {"feature": "trips", "limit": settings.GUEST_MAX_TRIPS, "used": int(count)},
            )

    # ------------------------------------------------------------------
    # Claiming
    # ------------------------------------------------------------------
    async def claim(self, *, token: Optional[str], user: User) -> ClaimResponse:
        if user.is_guest:
            raise ForbiddenError("Sign in to your account to keep this guest work.")
        session = await self._session_by_token(token, lock=True)   # row lock: two simultaneous claims run one after the other
        decision = rules.claim_decision(
            found=session is not None, status=session.status if session else None, expires_at=session.expires_at if session else None,
            claimed_by_user_id=session.claimed_by_user_id if session else None, user_id=user.id, now=_now(),
        )
        if decision in (rules.ClaimDecision.NOT_FOUND, rules.ClaimDecision.EXPIRED):
            raise GuestSessionExpiredError("That guest session has ended, so its trips can't be moved to your account.")
        if decision == rules.ClaimDecision.CLAIMED_BY_ANOTHER:
            raise ForbiddenError("That guest session can't be used with this account.")   # deliberately vague: no information about others
        assert session is not None
        if decision == rules.ClaimDecision.ALREADY_CLAIMED_BY_YOU:
            return self._summary_from(session.claim_summary, already=True)

        guest_id = session.guest_user_id
        trips = (await self.db.execute(
            select(Trip).where(Trip.owner_id == guest_id).order_by(Trip.updated_at.desc())
        )).scalars().all()
        claimed_trips = [ClaimedTrip(id=t.id, title=t.title, destination=t.destination, start_date=t.start_date, end_date=t.end_date) for t in trips]

        # Everything the guest owns moves to the account, inside this one transaction.
        await self.db.execute(update(Trip).where(Trip.owner_id == guest_id).values(owner_id=user.id))
        await self.db.execute(update(TripMember).where(TripMember.user_id == guest_id).values(user_id=user.id))
        await self.db.execute(update(TripVersion).where(TripVersion.created_by == guest_id).values(created_by=user.id))
        await self.db.execute(update(TripNote).where(TripNote.user_id == guest_id).values(user_id=user.id))
        await self.db.execute(update(TripCost).where(TripCost.added_by == guest_id).values(added_by=user.id))
        conversations = (await self.db.execute(update(Conversation).where(Conversation.user_id == guest_id).values(user_id=user.id))).rowcount or 0
        searches = (await self.db.execute(update(DiscoverySearch).where(DiscoverySearch.user_id == guest_id).values(user_id=user.id))).rowcount or 0

        summary = ClaimResponse(
            already_claimed=False, trips=claimed_trips, primary_trip_id=claimed_trips[0].id if claimed_trips else None,
            conversations=int(conversations), searches=int(searches),
        )
        session.status = "claimed"
        session.claimed_by_user_id = user.id
        session.claimed_at = _now()
        session.claim_summary = summary.model_dump(mode="json")
        guest_user = await self.db.get(User, guest_id)
        if guest_user is not None:
            guest_user.is_active = False               # the guest identity can never be used again
        await self.db.commit()
        logger.info("guest_session_claimed", session_id=str(session.id), trips=len(claimed_trips), conversations=int(conversations))
        return summary

    @staticmethod
    def _summary_from(raw: Optional[dict], *, already: bool) -> ClaimResponse:
        data = dict(raw or {"trips": [], "primary_trip_id": None, "conversations": 0, "searches": 0})
        data["already_claimed"] = already
        return ClaimResponse(**data)

    # ------------------------------------------------------------------
    # Cleanup (Celery beat)
    # ------------------------------------------------------------------
    async def cleanup_expired(self, batch: Optional[int] = None) -> dict[str, int]:
        """Delete unclaimed guest sessions that expired (their guest user cascades away with every trip, conversation and
        search), and the empty guest identities of sessions claimed more than a week ago. Safe to re-run."""
        now = _now()
        batch = batch or settings.GUEST_CLEANUP_BATCH
        expired = (await self.db.execute(
            select(GuestSession.guest_user_id).where(GuestSession.status == "active", GuestSession.expires_at < now).limit(batch)
        )).scalars().all()
        spent = (await self.db.execute(
            select(GuestSession.guest_user_id).where(
                GuestSession.status == "claimed", GuestSession.claimed_at < now - timedelta(days=_CLAIMED_USER_RETENTION_DAYS)
            ).limit(batch)
        )).scalars().all()
        ids = list(expired) + list(spent)
        if ids:
            await self.db.execute(delete(User).where(User.id.in_(ids), User.is_guest.is_(True)))   # DB cascades do the rest
            await self.db.commit()
        logger.info("guest_cleanup", expired=len(expired), claimed=len(spent))
        return {"expired_removed": len(expired), "claimed_removed": len(spent)}
