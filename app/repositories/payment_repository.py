"""Payment repository — payments, subscription events, webhook de-duplication,
and the subscription lookups the Paystack webhook handlers need."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PaymentStatus, SubscriptionStatus
from app.db.models.monetization import Subscription
from app.db.models.payment import Payment, PaymentWebhookEvent, SubscriptionEvent


class PaymentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Payments ---
    async def create_payment(self, payment: Payment) -> Payment:
        self.db.add(payment)
        await self.db.flush()
        return payment

    async def get_by_reference(self, reference: str, *, for_update: bool = False) -> Optional[Payment]:
        stmt = select(Payment).where(Payment.reference == reference)
        if for_update:
            # Serializes concurrent verify/webhook processing of one payment so
            # a subscription can never be activated twice for the same charge.
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, payment_id: uuid.UUID, *, for_update: bool = False) -> Optional[Payment]:
        stmt = select(Payment).where(Payment.id == payment_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_paid_payment_id(self, subscription_id: uuid.UUID) -> Optional[uuid.UUID]:
        """The most recent payment that actually CHARGED the customer for this
        subscription (paid_at is only ever set on a confirmed charge), whatever
        its status has since become — so the check still works for a payment
        that was just flipped to DISPUTED/REFUNDED. A refund/chargeback only
        cuts a subscription's access when it hits THIS payment: reversing an old
        month must not revoke the period the customer legitimately paid for since."""
        result = await self.db.execute(
            select(Payment.id)
            .where(Payment.subscription_id == subscription_id, Payment.paid_at.is_not(None))
            .order_by(Payment.paid_at.desc(), Payment.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_subscription(self, subscription_id: uuid.UUID) -> Optional[Subscription]:
        result = await self.db.execute(select(Subscription).where(Subscription.id == subscription_id))
        return result.scalar_one_or_none()

    async def refund_reference_seen(self, refund_reference: str) -> bool:
        """True if a refund with this provider reference was already applied
        (guards a re-sent refund.processed whose body differs slightly from the
        first, which the body-hash de-dup would not catch)."""
        result = await self.db.execute(
            select(func.count()).select_from(SubscriptionEvent).where(
                SubscriptionEvent.event_type == "refund.applied",
                SubscriptionEvent.details["refund_reference"].astext == refund_reference,
            )
        )
        return int(result.scalar_one()) > 0

    async def list_stale_pending(self, *, older_than: datetime, limit: int) -> Sequence[Payment]:
        """PENDING payments whose customer had time to finish (or abandon) checkout. Row-locked
        with SKIP LOCKED so overlapping reconcile runs never verify the same payment twice."""
        result = await self.db.execute(
            select(Payment)
            .where(Payment.status == PaymentStatus.PENDING, Payment.created_at <= older_than)
            .order_by(Payment.created_at).limit(limit).with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int, offset: int) -> Sequence[Payment]:
        result = await self.db.execute(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_all(
        self, *, status: Optional[PaymentStatus], limit: int, offset: int
    ) -> tuple[Sequence[Payment], int]:
        conditions = [Payment.status == status] if status is not None else []
        rows = await self.db.execute(
            select(Payment).where(*conditions).order_by(Payment.created_at.desc()).limit(limit).offset(offset)
        )
        total = await self.db.execute(select(func.count()).select_from(Payment).where(*conditions))
        return rows.scalars().all(), int(total.scalar_one())

    # --- Events ---
    async def add_event(self, event: SubscriptionEvent) -> SubscriptionEvent:
        self.db.add(event)
        await self.db.flush()
        return event

    async def find_subscription_create_event(
        self, customer_code: str, plan_code: str
    ) -> Optional[SubscriptionEvent]:
        """Latest recorded `subscription.create` webhook for this customer+plan
        (used when that webhook arrived BEFORE our subscription existed)."""
        result = await self.db.execute(
            select(SubscriptionEvent)
            .where(
                SubscriptionEvent.event_type == "paystack.subscription.create",
                SubscriptionEvent.details["customer_code"].astext == customer_code,
                SubscriptionEvent.details["plan_code"].astext == plan_code,
            )
            .order_by(SubscriptionEvent.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def register_webhook_event(self, event_key: str, event_type: str) -> bool:
        """True if this delivery is NEW; False if it was already recorded.
        Atomic (INSERT ... ON CONFLICT DO NOTHING): two concurrent deliveries
        of the same event cannot both win."""
        stmt = (
            pg_insert(PaymentWebhookEvent)
            .values(event_key=event_key, event_type=event_type)
            .on_conflict_do_nothing(index_elements=["event_key"])
            .returning(PaymentWebhookEvent.id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    # --- Subscription lookups ---
    async def get_subscription_by_provider_code(self, subscription_code: str) -> Optional[Subscription]:
        result = await self.db.execute(
            select(Subscription)
            .where(Subscription.paystack_subscription_code == subscription_code)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_current_subscription(
        self, *, customer_code: str, plan_id: uuid.UUID
    ) -> Optional[Subscription]:
        """The ACTIVE provider-managed subscription for this customer+plan,
        including one whose period just lapsed while a renewal was in flight."""
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.paystack_customer_code == customer_code,
                Subscription.plan_id == plan_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)
