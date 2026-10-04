"""
SubscriptionService (Master Blueprint §48).

Payment collection is Paystack (see app/modules/payments). A user can
self-subscribe only to a FREE plan; paid plans are activated exclusively by
`activate_paid_subscription`, which the payment path calls AFTER the charge
has been verified with the provider — never from a client request
(Blueprint §108: "Do NOT pretend a payment succeeded").

A subscription is "current" only while `status == ACTIVE` AND
`current_period_end` is in the future (see MonetizationRepository).
Cancelling a PAID subscription stops renewal but keeps access until the
period the user already paid for ends.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BillingInterval, SubscriptionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PaymentRequiredError,
    ValidationAppError,
)
from app.db.models.monetization import Plan, Subscription
from app.db.models.payment import SubscriptionEvent
from app.modules.subscriptions.schemas import PlanCreateRequest, PlanUpdateRequest
from app.providers.payments.interface import PaymentProvider
from app.repositories.monetization_repository import MonetizationRepository
from app.repositories.payment_repository import PaymentRepository

_INTERVAL_DAYS = {
    BillingInterval.MONTHLY: 30,
    BillingInterval.YEARLY: 365,
    BillingInterval.FREE: 36500,  # effectively indefinite (100 years)
}


def period_length(plan: Plan) -> timedelta:
    return timedelta(days=_INTERVAL_DAYS.get(plan.billing_interval, 30))


class SubscriptionService:
    def __init__(self, db: AsyncSession, payment_provider: Optional[PaymentProvider] = None):
        self.db = db
        self.repo = MonetizationRepository(db)
        self.payment_repo = PaymentRepository(db)
        self._payment_provider = payment_provider

    def _provider(self) -> PaymentProvider:
        if self._payment_provider is None:
            from app.providers.payments.factory import get_payment_provider

            self._payment_provider = get_payment_provider()
        return self._payment_provider

    # --- Plans (admin) ---
    async def create_plan(self, payload: PlanCreateRequest) -> Plan:
        if await self.repo.get_plan_by_slug(payload.slug):
            raise ConflictError(f"A plan with slug '{payload.slug}' already exists.")
        await self._validate_paystack_code(
            plan_code=payload.paystack_plan_code,
            price_amount=payload.price_amount,
            billing_interval=payload.billing_interval,
        )

        plan = Plan(
            name=payload.name,
            slug=payload.slug,
            price_amount=payload.price_amount,
            price_currency=payload.price_currency.upper(),
            billing_interval=payload.billing_interval,
            included_feature_flags=[f.value for f in payload.included_feature_flags],
            paystack_plan_code=payload.paystack_plan_code,
            is_active=True,
        )
        await self.repo.create_plan(plan)
        await self.db.commit()
        return plan

    async def update_plan(self, plan_id: uuid.UUID, payload: PlanUpdateRequest) -> Plan:
        """Attach/replace the Paystack plan code or (de)activate a plan.
        Price, currency and interval are immutable once created: changing them
        would silently change what existing subscribers are charged."""
        plan = await self.repo.get_plan(plan_id)
        if plan is None:
            raise NotFoundError("Plan not found.")

        changes = payload.model_dump(exclude_unset=True)
        if "paystack_plan_code" in changes and changes["paystack_plan_code"] != plan.paystack_plan_code:
            await self._validate_paystack_code(
                plan_code=changes["paystack_plan_code"],
                price_amount=plan.price_amount,
                billing_interval=plan.billing_interval,
            )
            plan.paystack_plan_code = changes["paystack_plan_code"]
        if "is_active" in changes and changes["is_active"] is not None:
            plan.is_active = changes["is_active"]

        await self.db.flush()
        await self.db.commit()
        return plan

    async def _validate_paystack_code(
        self, *, plan_code: Optional[str], price_amount: float, billing_interval: BillingInterval
    ) -> None:
        if not plan_code:
            return
        if price_amount <= 0 or billing_interval == BillingInterval.FREE:
            raise ValidationAppError("A free plan cannot have a Paystack plan code.")
        if await self.repo.get_plan_by_paystack_code(plan_code) is not None:
            raise ConflictError("This Paystack plan code is already linked to another plan.")

    async def list_active_plans(self):
        return await self.repo.list_active_plans()

    async def get_my_subscription(self, user_id: uuid.UUID) -> Optional[Subscription]:
        return await self.repo.get_active_subscription(user_id)

    # --- Activation ---
    async def subscribe(self, *, user_id: uuid.UUID, plan_id: uuid.UUID) -> Subscription:
        """User-initiated subscription. FREE plans only — a paid plan needs
        a confirmed payment first, so it is refused here."""
        plan = await self.repo.get_plan(plan_id)
        if plan is None or not plan.is_active:
            raise NotFoundError("Plan not found.")

        if plan.price_amount > 0:
            raise PaymentRequiredError(
                "This plan requires payment. Complete checkout to subscribe.",
                details={"plan_id": str(plan.id), "price_amount": plan.price_amount,
                         "price_currency": plan.price_currency},
            )
        return await self._activate(user_id=user_id, plan=plan)

    async def activate_paid_subscription(
        self,
        *,
        user_id: uuid.UUID,
        plan_id: uuid.UUID,
        provider_customer_code: Optional[str] = None,
        commit: bool = True,
    ) -> Subscription:
        """Called ONLY by the payment-confirmation path after the charge has
        been verified with the payment provider. `commit=False` lets that path
        record the payment, the subscription and their events atomically in
        one transaction."""
        plan = await self.repo.get_plan(plan_id)
        if plan is None or not plan.is_active:
            raise NotFoundError("Plan not found.")
        return await self._activate(
            user_id=user_id,
            plan=plan,
            provider_customer_code=provider_customer_code,
            auto_renew=bool(plan.paystack_plan_code),
            commit=commit,
        )

    async def _activate(
        self,
        *,
        user_id: uuid.UUID,
        plan: Plan,
        provider_customer_code: Optional[str] = None,
        auto_renew: bool = False,
        commit: bool = True,
    ) -> Subscription:
        # Only one active subscription at a time — cancel any existing
        # one before activating the new plan.
        existing = await self.repo.get_active_subscription(user_id)
        if existing is not None:
            existing.status = SubscriptionStatus.CANCELLED
            existing.cancelled_at = datetime.now(timezone.utc)
            await self.repo.save_subscription(existing)

        now = datetime.now(timezone.utc)
        subscription = Subscription(
            user_id=user_id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
            current_period_start=now, current_period_end=now + period_length(plan),
            auto_renew=auto_renew, paystack_customer_code=provider_customer_code,
        )
        await self.repo.create_subscription(subscription)
        await self.payment_repo.add_event(
            SubscriptionEvent(
                user_id=user_id, subscription_id=subscription.id, event_type="subscription.activated",
                details={"plan_id": str(plan.id), "auto_renew": auto_renew},
            )
        )
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return subscription

    # --- Cancellation ---
    async def cancel_my_subscription(self, user_id: uuid.UUID) -> Subscription:
        subscription = await self.repo.get_active_subscription(user_id)
        if subscription is None:
            raise ValidationAppError("You do not have an active subscription to cancel.")

        plan = await self.repo.get_plan(subscription.plan_id)
        now = datetime.now(timezone.utc)

        # Free plan: nothing was paid, so it simply ends now.
        if plan is None or plan.price_amount <= 0:
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancelled_at = now
            await self.repo.save_subscription(subscription)
            await self.db.commit()
            return subscription

        if subscription.cancelled_at is not None:
            return subscription  # already cancelled; access continues to period end

        # Provider-managed recurring plan: stop renewals AT THE PROVIDER first.
        # If that fails we must not mark it cancelled locally, or the customer
        # would keep being charged for a subscription that looks cancelled.
        if plan.paystack_plan_code and subscription.auto_renew:
            if not (subscription.paystack_subscription_code and subscription.paystack_email_token):
                raise ConflictError(
                    "Your subscription is still being set up with the payment provider. "
                    "Please try again in a few minutes."
                )
            await self._provider().disable_subscription(
                subscription_code=subscription.paystack_subscription_code,
                email_token=subscription.paystack_email_token,
            )

        subscription.auto_renew = False
        subscription.cancelled_at = now
        await self.repo.save_subscription(subscription)
        await self.payment_repo.add_event(
            SubscriptionEvent(
                user_id=user_id, subscription_id=subscription.id, event_type="subscription.cancelled_by_user",
                details={"access_until": subscription.current_period_end.isoformat()},
            )
        )
        await self.db.commit()
        return subscription
