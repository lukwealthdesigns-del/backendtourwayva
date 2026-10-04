"""
PaymentService — Paystack checkout, verification and webhooks.

Trust model (Master Prompt Principle 1 — never trust the frontend):

  * The client only ever says "I want plan X". The AMOUNT and CURRENCY come
    from OUR plan record, never from the request.
  * A payment is applied only after the provider CONFIRMS it (verify API or a
    signed webhook), and only if the confirmed amount and currency equal what
    we asked for. A mismatch is recorded as a security event and never
    activates anything.
  * Webhooks are authenticated by an HMAC-SHA512 signature over the raw body
    and de-duplicated by the body's SHA-256, so redelivery is a no-op.
  * Applying a payment is idempotent and serialized (row lock on the payment)
    so the two arrival paths — the user's redirect calling /payments/verify
    and Paystack's webhook — can race without activating twice.

Flows
-----
  create_checkout     -> pending Payment + Paystack authorization_url
  verify_and_apply    -> user returns from checkout; verified server-to-server
  handle_webhook      -> charge.success / subscription.create / subscription.disable
                         / subscription.not_renew / invoice.payment_failed
  renewals            -> a charge.success whose reference we did not create, for
                         a provider-managed plan: matched to the customer's
                         subscription and its period extended
  refund_payment      -> admin asks Paystack to refund (part of) a payment;
                         Paystack refunds are ASYNCHRONOUS, so nothing changes
                         locally until the signed refund.processed webhook
  refund.processed    -> refunded amount recorded; a FULL refund of the payment
                         that funds the current period ends the subscription
  charge.dispute.*    -> a chargeback opens: payment marked DISPUTED and access
                         revoked at once (fail toward not granting access). The
                         dispute RESOLUTION is recorded but never auto-restores
                         anything — a human decides.

Webhook source: on top of the HMAC signature, requests are checked against
PAYSTACK_WEBHOOK_IP_ALLOWLIST (logged + security event by default, rejected
with 403 when PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST is on).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import AuditResult, NotificationType, PaymentStatus, SecurityEventSeverity, SubscriptionStatus
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ProviderUnavailableError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.db.models.monetization import Plan, Subscription
from app.db.models.payment import Payment, SubscriptionEvent
from app.db.models.user import User
from app.modules.subscriptions.service import SubscriptionService, period_length
from app.providers.payments.factory import get_payment_provider
from app.providers.payments.interface import PaymentProvider, VerifiedTransaction
from app.providers.payments.paystack_parsing import (
    extract_transaction_reference,
    parse_positive_minor_amount,
    parse_transaction,
)
from app.repositories.monetization_repository import MonetizationRepository
from app.repositories.payment_repository import PaymentRepository
from app.utils.money import to_minor_units

logger = get_logger(__name__)


@dataclass(frozen=True)
class CheckoutResult:
    authorization_url: str
    reference: str
    access_code: str
    public_key: Optional[str]


@dataclass(frozen=True)
class RefundInitiation:
    payment_id: uuid.UUID
    reference: str
    requested_amount_minor: int
    provider_status: str


def _clip(value: Optional[str], limit: int = 255) -> Optional[str]:
    return value[:limit] if value else value


class PaymentService:
    def __init__(self, db: AsyncSession, provider: Optional[PaymentProvider] = None):
        self.db = db
        self.provider = provider or get_payment_provider()
        self.repo = PaymentRepository(db)
        self.monetization = MonetizationRepository(db)
        self.subscriptions = SubscriptionService(db, payment_provider=self.provider)

    # ------------------------------------------------------------------
    # Checkout
    # ------------------------------------------------------------------
    async def create_checkout(self, user: User, plan_id: uuid.UUID) -> CheckoutResult:
        plan = await self.monetization.get_plan(plan_id)
        if plan is None or not plan.is_active:
            raise NotFoundError("Plan not found.")
        if plan.price_amount <= 0:
            raise ValidationAppError("This plan is free — no payment is needed.")

        currency = plan.price_currency.upper()
        if currency not in settings.PAYSTACK_SUPPORTED_CURRENCIES:
            raise ValidationAppError(
                f"Payments in {currency} are not enabled.",
                details={"supported_currencies": settings.PAYSTACK_SUPPORTED_CURRENCIES},
            )
        if not settings.paystack_configured:
            raise ProviderUnavailableError("Payments are not configured.")

        amount_minor = to_minor_units(plan.price_amount)
        # Paystack references allow only letters, digits, "-", "." and "=".
        reference = f"TW-{uuid.uuid4().hex}"

        payment = Payment(
            user_id=user.id, plan_id=plan.id, provider="paystack", reference=reference,
            amount_minor=amount_minor, currency=currency, status=PaymentStatus.PENDING, is_renewal=False,
        )
        await self.repo.create_payment(payment)
        # Committed BEFORE calling the provider so a fast webhook can find it.
        await self.db.commit()

        try:
            session = await self.provider.initialize_transaction(
                email=user.email,
                amount_minor=amount_minor,
                currency=currency,
                reference=reference,
                callback_url=settings.PAYSTACK_CALLBACK_URL or None,
                metadata={"user_id": str(user.id), "plan_id": str(plan.id), "payment_id": str(payment.id)},
                plan_code=plan.paystack_plan_code,
            )
        except Exception:
            payment.status = PaymentStatus.FAILED
            payment.gateway_response = "initialize_failed"
            await self.db.commit()
            raise

        return CheckoutResult(
            authorization_url=session.authorization_url,
            reference=session.reference,
            access_code=session.access_code,
            public_key=settings.PAYSTACK_PUBLIC_KEY or None,
        )

    # ------------------------------------------------------------------
    # Verification (user returns from checkout)
    # ------------------------------------------------------------------
    async def verify_and_apply(self, *, reference: str, user: User) -> Payment:
        payment = await self.repo.get_by_reference(reference, for_update=True)
        # Same answer for "does not exist" and "belongs to someone else".
        if payment is None or payment.user_id != user.id:
            raise NotFoundError("Payment not found.")
        if payment.status == PaymentStatus.SUCCESS:
            return payment

        verified = await self.provider.verify_transaction(reference)
        await self._apply_verified(payment, verified)
        return payment

    async def list_my_payments(self, user_id: uuid.UUID, *, limit: int, offset: int):
        return await self.repo.list_for_user(user_id, limit=limit, offset=offset)

    async def list_all_payments(self, *, status: Optional[PaymentStatus], limit: int, offset: int):
        return await self.repo.list_all(status=status, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Reconciliation (scheduled): catch payments whose webhook never arrived
    # ------------------------------------------------------------------
    async def reconcile_pending(self, *, limit: int = 100) -> dict[str, int]:
        """Ask the provider about every PENDING payment older than
        PAYMENT_RECONCILE_MIN_AGE_MINUTES. A customer who paid but whose browser never returned
        and whose webhook was lost would otherwise pay and get nothing. Payments still unpaid
        after PAYMENT_RECONCILE_MAX_AGE_HOURS are closed as ABANDONED. Idempotent: the normal
        apply path (amount/currency check, atomic activation) does the work."""
        now = datetime.now(timezone.utc)
        counts = {"checked": 0, "applied": 0, "abandoned": 0, "errors": 0}
        cutoff = now - timedelta(minutes=settings.PAYMENT_RECONCILE_MIN_AGE_MINUTES)
        max_age = timedelta(hours=settings.PAYMENT_RECONCILE_MAX_AGE_HOURS)

        for payment in await self.repo.list_stale_pending(older_than=cutoff, limit=limit):
            counts["checked"] += 1
            try:
                verified = await self.provider.verify_transaction(payment.reference)
                await self._apply_verified(payment, verified)
                if payment.status == PaymentStatus.SUCCESS:
                    counts["applied"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad payment must not stop the sweep
                await self.db.rollback()
                counts["errors"] += 1
                logger.warning("payment_reconcile_failed", reference=payment.reference, error=str(exc))
                continue

            if payment.status == PaymentStatus.PENDING and now - payment.created_at > max_age:
                payment.status = PaymentStatus.ABANDONED
                payment.gateway_response = "timed_out"
                await self.db.commit()
                counts["abandoned"] += 1
        return counts

    # ------------------------------------------------------------------
    # Applying a provider-confirmed transaction
    # ------------------------------------------------------------------
    async def _apply_verified(self, payment: Payment, verified: VerifiedTransaction) -> None:
        if verified.status == "success":
            await self._apply_success(payment, verified)
        elif verified.status in ("failed", "reversed"):
            payment.status = PaymentStatus.FAILED
            payment.gateway_response = _clip(verified.gateway_response)
            await self.db.commit()
        elif verified.status == "abandoned":
            payment.status = PaymentStatus.ABANDONED
            payment.gateway_response = _clip(verified.gateway_response)
            await self.db.commit()
        # any other status (ongoing / pending / processing / queued): still in flight.

    async def _apply_success(self, payment: Payment, verified: VerifiedTransaction) -> None:
        from app.modules.security.service import SecurityService

        if verified.amount_minor != payment.amount_minor or verified.currency != payment.currency:
            payment.status = PaymentStatus.FAILED
            payment.gateway_response = "amount_or_currency_mismatch"
            await self.repo.add_event(
                SubscriptionEvent(
                    user_id=payment.user_id, event_type="payment.amount_mismatch",
                    provider_reference=payment.reference,
                    details={
                        "expected": {"amount_minor": payment.amount_minor, "currency": payment.currency},
                        "received": {"amount_minor": verified.amount_minor, "currency": verified.currency},
                    },
                )
            )
            await self.db.commit()
            logger.error("payment_amount_mismatch", reference=payment.reference)
            await SecurityService(self.db).record_event(
                user_id=payment.user_id,
                event_type="payment_amount_mismatch",
                severity=SecurityEventSeverity.CRITICAL,
                metadata={"reference": payment.reference},
            )
            return

        plan = await self.monetization.get_plan(payment.plan_id)
        previous = await self.monetization.get_active_subscription(payment.user_id)
        previous_codes = (
            (previous.paystack_subscription_code, previous.paystack_email_token)
            if previous is not None and previous.auto_renew else None
        )

        payment.status = PaymentStatus.SUCCESS
        payment.paid_at = verified.paid_at or datetime.now(timezone.utc)
        payment.gateway_response = _clip(verified.gateway_response)
        payment.provider_customer_code = verified.customer_code

        # Atomic: payment + subscription + events commit together.
        subscription = await self.subscriptions.activate_paid_subscription(
            user_id=payment.user_id,
            plan_id=payment.plan_id,
            provider_customer_code=verified.customer_code,
            commit=False,
        )
        payment.subscription_id = subscription.id
        await self._attach_pending_subscription_codes(subscription, plan, verified.customer_code)
        await self.repo.add_event(
            SubscriptionEvent(
                user_id=payment.user_id, subscription_id=subscription.id, event_type="payment.succeeded",
                provider_reference=payment.reference,
                details={"amount_minor": payment.amount_minor, "currency": payment.currency},
            )
        )
        await self.db.commit()

        # Post-commit, best effort: none of this may undo a confirmed payment.
        await self._disable_replaced_subscription(previous, previous_codes)
        await self._notify(
            payment.user_id,
            title="Payment received",
            body="Thank you! Your Tour-Wayva subscription is now active.",
        )

    async def _attach_pending_subscription_codes(
        self, subscription: Subscription, plan: Optional[Plan], customer_code: Optional[str]
    ) -> None:
        """`subscription.create` can reach us before the first charge has been
        applied. If we parked its codes in an event, attach them now (and drop
        the token from the event — it is only needed on the subscription)."""
        if plan is None or not plan.paystack_plan_code or not customer_code:
            return
        event = await self.repo.find_subscription_create_event(customer_code, plan.paystack_plan_code)
        if event is None or not event.details or event.details.get("attached"):
            return
        subscription.paystack_subscription_code = event.details.get("subscription_code")
        subscription.paystack_email_token = event.details.get("email_token")
        event.details = {**event.details, "email_token": None, "attached": True}

    async def _disable_replaced_subscription(
        self, previous: Optional[Subscription], codes: Optional[tuple[Optional[str], Optional[str]]]
    ) -> None:
        """A user who buys another plan must not keep being billed for the old
        auto-renewing one."""
        if previous is None or not codes or not all(codes):
            return
        try:
            await self.provider.disable_subscription(subscription_code=codes[0], email_token=codes[1])
        except Exception as exc:  # noqa: BLE001
            logger.error("replaced_subscription_disable_failed", subscription_id=str(previous.id), error=str(exc))
            await self.repo.add_event(
                SubscriptionEvent(
                    user_id=previous.user_id, subscription_id=previous.id,
                    event_type="subscription.replaced_disable_failed", details={"error": str(exc)[:200]},
                )
            )
            await self.db.commit()

    async def _notify(self, user_id: uuid.UUID, *, title: str, body: str) -> None:
        try:
            from app.modules.notifications.service import NotificationService

            await NotificationService(self.db).notify(
                user_id=user_id, notification_type=NotificationType.SUBSCRIPTION_EVENT,
                title=title, body=body, send_email=True,
            )
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            logger.warning("payment_notification_failed", user_id=str(user_id), error=str(exc))

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    async def _check_webhook_source(self, client_ip: Optional[str]) -> None:
        """Defense in depth on top of the HMAC signature (which stays the
        authoritative check): is this request coming from an IP Paystack
        publishes for webhook delivery? Runs AFTER signature verification, so
        random scanners (401 already) never generate security events."""
        allowlist = {ip.strip() for ip in settings.PAYSTACK_WEBHOOK_IP_ALLOWLIST if ip and ip.strip()}
        if not allowlist or (client_ip is not None and client_ip in allowlist):
            return

        from app.modules.security.service import SecurityService

        enforced = settings.PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST
        logger.warning("paystack_webhook_unlisted_ip", ip=client_ip, enforced=enforced)
        await SecurityService(self.db).record_event(
            user_id=None, event_type="paystack_webhook_unlisted_ip", severity=SecurityEventSeverity.WARNING,
            ip_address=client_ip, metadata={"enforced": enforced},
        )
        if enforced:
            raise ForbiddenError("Webhook source is not allowed.")

    async def handle_webhook(
        self, *, raw_body: bytes, signature: Optional[str], client_ip: Optional[str] = None
    ) -> str:
        if not self.provider.verify_webhook_signature(raw_body=raw_body, signature=signature):
            raise UnauthorizedError("Invalid webhook signature.")
        await self._check_webhook_source(client_ip)

        try:
            event = json.loads(raw_body)
        except ValueError as exc:
            raise ValidationAppError("Invalid webhook payload.") from exc
        if not isinstance(event, dict):
            raise ValidationAppError("Invalid webhook payload.")

        event_type = str(event.get("event") or "")[:80]
        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        event_key = hashlib.sha256(raw_body).hexdigest()
        if not await self.repo.register_webhook_event(event_key, event_type):
            return "duplicate"

        handlers = {
            "charge.success": self._on_charge_success,
            "subscription.create": self._on_subscription_create,
            "subscription.disable": self._on_subscription_stopped,
            "subscription.not_renew": self._on_subscription_stopped,
            "invoice.payment_failed": self._on_invoice_payment_failed,
            "refund.processed": self._on_refund_processed,
            "refund.failed": self._on_refund_failed,
            "charge.dispute.create": self._on_dispute_created,
            "charge.dispute.resolve": self._on_dispute_resolved,
        }
        handler = handlers.get(event_type)
        if handler is None:
            await self.db.commit()  # keep the de-dup record
            return "ignored"

        result = await handler(data)
        await self.db.commit()
        return result

    async def _on_charge_success(self, data: dict[str, Any]) -> str:
        transaction = parse_transaction(data)
        if not transaction.reference:
            return "ignored"

        payment = await self.repo.get_by_reference(transaction.reference, for_update=True)
        if payment is not None:
            if payment.status != PaymentStatus.SUCCESS:
                await self._apply_verified(payment, transaction)
            return "processed"
        return await self._apply_renewal(transaction)

    async def _apply_renewal(self, transaction: VerifiedTransaction) -> str:
        from app.modules.security.service import SecurityService

        if not transaction.plan_code or not transaction.customer_code:
            await self._record(None, "charge.unmatched", transaction.reference, {"reason": "no plan/customer"})
            return "unmatched"

        plan = await self.monetization.get_plan_by_paystack_code(transaction.plan_code)
        subscription = (
            await self.repo.find_current_subscription(customer_code=transaction.customer_code, plan_id=plan.id)
            if plan is not None else None
        )
        if plan is None or subscription is None:
            await self._record(
                None, "renewal.unmatched", transaction.reference,
                {"plan_code": transaction.plan_code, "customer_code": transaction.customer_code},
            )
            return "unmatched"

        if transaction.amount_minor != to_minor_units(plan.price_amount) or transaction.currency != plan.price_currency.upper():
            await self._record(
                subscription.user_id, "renewal.amount_mismatch", transaction.reference,
                {"expected_minor": to_minor_units(plan.price_amount), "received_minor": transaction.amount_minor,
                 "currency": transaction.currency},
                subscription_id=subscription.id,
            )
            await self.db.commit()
            await SecurityService(self.db).record_event(
                user_id=subscription.user_id, event_type="renewal_amount_mismatch",
                severity=SecurityEventSeverity.CRITICAL, metadata={"reference": transaction.reference},
            )
            return "rejected"

        now = datetime.now(timezone.utc)
        await self.repo.create_payment(
            Payment(
                user_id=subscription.user_id, plan_id=plan.id, subscription_id=subscription.id, provider="paystack",
                reference=transaction.reference, amount_minor=transaction.amount_minor,
                currency=transaction.currency, status=PaymentStatus.SUCCESS, is_renewal=True,
                paid_at=transaction.paid_at or now, gateway_response=_clip(transaction.gateway_response),
                provider_customer_code=transaction.customer_code,
            )
        )
        # Extend from whichever is later: the current end, or now (a renewal
        # that lands just after the period lapsed).
        base = max(subscription.current_period_end, now)
        subscription.current_period_end = base + period_length(plan)
        subscription.auto_renew = True
        await self._record(
            subscription.user_id, "subscription.renewed", transaction.reference,
            {"new_period_end": subscription.current_period_end.isoformat()}, subscription_id=subscription.id,
        )
        return "processed"

    async def _on_subscription_create(self, data: dict[str, Any]) -> str:
        code = data.get("subscription_code")
        token = data.get("email_token")
        customer_code = (data.get("customer") or {}).get("customer_code") if isinstance(data.get("customer"), dict) else None
        plan_code = (data.get("plan") or {}).get("plan_code") if isinstance(data.get("plan"), dict) else None
        if not (code and token and customer_code and plan_code):
            return "ignored"

        plan = await self.monetization.get_plan_by_paystack_code(plan_code)
        subscription = (
            await self.repo.find_current_subscription(customer_code=customer_code, plan_id=plan.id)
            if plan is not None else None
        )
        if subscription is not None:
            subscription.paystack_subscription_code = code
            subscription.paystack_email_token = token
            await self._record(
                subscription.user_id, "paystack.subscription.create", None,
                {"customer_code": customer_code, "plan_code": plan_code, "attached": True},
                subscription_id=subscription.id,
            )
            return "processed"

        # Arrived before the first charge was applied: park the codes.
        await self._record(
            None, "paystack.subscription.create", None,
            {"customer_code": customer_code, "plan_code": plan_code, "subscription_code": code,
             "email_token": token, "attached": False},
        )
        return "processed"

    async def _on_subscription_stopped(self, data: dict[str, Any]) -> str:
        """subscription.disable / subscription.not_renew: renewals will stop.
        The user keeps access until the period they already paid for ends."""
        code = data.get("subscription_code")
        subscription = await self.repo.get_subscription_by_provider_code(code) if code else None
        if subscription is None:
            await self._record(None, "paystack.subscription.stopped_unmatched", None, {"subscription_code": code})
            return "unmatched"
        subscription.auto_renew = False
        if subscription.cancelled_at is None:
            subscription.cancelled_at = datetime.now(timezone.utc)
        await self._record(
            subscription.user_id, "paystack.subscription.stopped", None,
            {"access_until": subscription.current_period_end.isoformat()}, subscription_id=subscription.id,
        )
        return "processed"

    async def _on_invoice_payment_failed(self, data: dict[str, Any]) -> str:
        sub_info = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
        code = sub_info.get("subscription_code")
        subscription = await self.repo.get_subscription_by_provider_code(code) if code else None
        if subscription is None:
            await self._record(None, "paystack.invoice.payment_failed_unmatched", None, {"subscription_code": code})
            return "unmatched"
        await self._record(
            subscription.user_id, "paystack.invoice.payment_failed", None,
            {"access_until": subscription.current_period_end.isoformat()}, subscription_id=subscription.id,
        )
        await self.db.commit()
        await self._notify(
            subscription.user_id,
            title="We couldn't renew your subscription",
            body="Your latest payment did not go through. Please update your payment method to keep your plan.",
        )
        return "processed"

    # ------------------------------------------------------------------
    # Refunds and chargebacks
    # ------------------------------------------------------------------
    async def refund_payment(
        self,
        *,
        payment_id: uuid.UUID,
        actor_id: uuid.UUID,
        amount_minor: Optional[int],
        reason: str,
        ip_address: Optional[str] = None,
    ) -> RefundInitiation:
        """Admin-initiated refund. Asks Paystack to refund and records the
        request; it does NOT change the payment locally — Paystack refunds are
        asynchronous (pending -> processed/failed), so the payment only flips
        to REFUNDED (and access is only cut) when the signed `refund.processed`
        webhook arrives. Never claims a refund happened that Paystack has not
        confirmed (Blueprint §108)."""
        from app.modules.admin.admin_service import AdminService

        admin = AdminService(self.db)
        payment = await self.repo.get_by_id(payment_id, for_update=True)
        if payment is None:
            raise NotFoundError("Payment not found.")
        if payment.status != PaymentStatus.SUCCESS:
            raise ConflictError(f"Only successful payments can be refunded (this one is {payment.status.value}).")

        remaining = payment.amount_minor - payment.refunded_amount_minor
        if remaining <= 0:
            raise ConflictError("This payment has already been fully refunded.")
        requested = remaining if amount_minor is None else amount_minor
        if requested <= 0 or requested > remaining:
            raise ValidationAppError(
                "Refund amount must be between 1 and the remaining refundable amount.",
                details={"remaining_refundable_minor": remaining, "currency": payment.currency},
            )

        audit_meta = {"reference": payment.reference, "amount_minor": requested, "currency": payment.currency}
        try:
            result = await self.provider.refund_transaction(
                reference=payment.reference, amount_minor=requested, reason=reason
            )
        except Exception as exc:
            # ProviderUnavailableError can mean the request WAS accepted but the
            # response was lost, so the audit trail says so — an admin should
            # check the Paystack dashboard before retrying.
            await admin.log(
                admin_user_id=actor_id, action="payment.refund", target_type="payment", target_id=str(payment.id),
                result=AuditResult.FAILURE, reason=reason, ip_address=ip_address,
                metadata={**audit_meta, "error": str(exc)[:200]},
            )
            await self.db.commit()
            raise

        await self._record(
            payment.user_id, "refund.initiated", payment.reference,
            {"amount_minor": requested, "actor_id": str(actor_id), "reason": reason[:200],
             "provider_status": result.status, "refund_id": result.refund_id},
            subscription_id=payment.subscription_id,
        )
        await admin.log(
            admin_user_id=actor_id, action="payment.refund", target_type="payment", target_id=str(payment.id),
            result=AuditResult.SUCCESS, reason=reason, ip_address=ip_address,
            metadata={**audit_meta, "provider_status": result.status},
        )
        await self.db.commit()
        return RefundInitiation(
            payment_id=payment.id, reference=payment.reference, requested_amount_minor=requested,
            provider_status=result.status,
        )

    async def _on_refund_processed(self, data: dict[str, Any]) -> str:
        from app.modules.security.service import SecurityService

        reference = extract_transaction_reference(data)
        amount = parse_positive_minor_amount(data.get("amount"))
        refund_reference = str(data.get("refund_reference") or data.get("id") or "")

        payment = await self.repo.get_by_reference(reference, for_update=True) if reference else None
        if payment is None or amount is None:
            # Never guess which payment (or how much) — park it for manual reconciliation.
            await self._record(
                None, "refund.unmatched", reference or None,
                {"reason": "payment not found" if payment is None else "missing or invalid amount",
                 "refund_reference": refund_reference or None},
            )
            return "unmatched"
        if refund_reference and await self.repo.refund_reference_seen(refund_reference):
            return "duplicate"
        if payment.status not in (PaymentStatus.SUCCESS, PaymentStatus.DISPUTED):
            await self._record(
                payment.user_id, "refund.unexpected_state", payment.reference,
                {"status": payment.status.value, "amount_minor": amount}, subscription_id=payment.subscription_id,
            )
            return "ignored"
        currency = str(data.get("currency") or "").upper()
        if currency and currency != payment.currency:
            await self._record(
                payment.user_id, "refund.currency_mismatch", payment.reference,
                {"expected": payment.currency, "received": currency}, subscription_id=payment.subscription_id,
            )
            await self.db.commit()
            await SecurityService(self.db).record_event(
                user_id=payment.user_id, event_type="refund_currency_mismatch",
                severity=SecurityEventSeverity.CRITICAL, metadata={"reference": payment.reference},
            )
            return "rejected"

        total = min(payment.refunded_amount_minor + amount, payment.amount_minor)
        clamped = payment.refunded_amount_minor + amount > payment.amount_minor
        payment.refunded_amount_minor = total
        payment.refunded_at = datetime.now(timezone.utc)
        is_full = total >= payment.amount_minor
        if is_full:
            payment.status = PaymentStatus.REFUNDED
        await self._record(
            payment.user_id, "refund.applied", payment.reference,
            {"refund_reference": refund_reference or None, "amount_minor": amount,
             "total_refunded_minor": total, "full": is_full, "clamped": clamped},
            subscription_id=payment.subscription_id,
        )
        if is_full:
            await self._revoke_access_for_payment(payment, reason="refunded")
        else:
            await self.db.commit()
            await self._notify(
                payment.user_id, title="Refund processed",
                body="A partial refund for your Tour-Wayva payment has been processed.",
            )
        return "processed"

    async def _on_refund_failed(self, data: dict[str, Any]) -> str:
        reference = extract_transaction_reference(data)
        payment = await self.repo.get_by_reference(reference) if reference else None
        await self._record(
            payment.user_id if payment else None, "refund.failed", reference or None,
            {"refund_reference": str(data.get("refund_reference") or data.get("id") or "") or None,
             "amount_minor": parse_positive_minor_amount(data.get("amount"))},
            subscription_id=payment.subscription_id if payment else None,
        )
        return "processed" if payment else "unmatched"

    async def _on_dispute_created(self, data: dict[str, Any]) -> str:
        from app.modules.security.service import SecurityService

        reference = extract_transaction_reference(data)
        payment = await self.repo.get_by_reference(reference, for_update=True) if reference else None
        if payment is None:
            await self._record(None, "dispute.unmatched", reference or None, {"dispute_id": data.get("id")})
            await self.db.commit()
            await SecurityService(self.db).record_event(
                user_id=None, event_type="payment_dispute_unmatched", severity=SecurityEventSeverity.WARNING,
                metadata={"reference": reference or None},
            )
            return "unmatched"
        if payment.status == PaymentStatus.REFUNDED:
            await self._record(
                payment.user_id, "dispute.on_refunded_payment", payment.reference, {"dispute_id": data.get("id")},
                subscription_id=payment.subscription_id,
            )
            return "processed"

        if payment.status == PaymentStatus.SUCCESS:
            payment.status = PaymentStatus.DISPUTED
        await self._record(
            payment.user_id, "payment.disputed", payment.reference,
            {"dispute_id": data.get("id"), "dispute_status": data.get("status"), "category": data.get("category")},
            subscription_id=payment.subscription_id,
        )
        await self._revoke_access_for_payment(payment, reason="disputed")
        await SecurityService(self.db).record_event(
            user_id=payment.user_id, event_type="payment_disputed", severity=SecurityEventSeverity.CRITICAL,
            metadata={"reference": payment.reference},
        )
        return "processed"

    async def _on_dispute_resolved(self, data: dict[str, Any]) -> str:
        """Records the outcome for the admin dashboard but deliberately does
        NOT restore the payment or the customer's access on its own: the
        resolution vocabulary is Paystack's, the money movement is on their
        side, and re-granting access on a misread string is the wrong way to
        fail. An admin reviews and re-activates manually if warranted."""
        reference = extract_transaction_reference(data)
        payment = await self.repo.get_by_reference(reference) if reference else None
        await self._record(
            payment.user_id if payment else None, "dispute.resolved", reference or None,
            {"dispute_id": data.get("id"), "status": data.get("status"), "resolution": data.get("resolution")},
            subscription_id=payment.subscription_id if payment else None,
        )
        return "processed" if payment else "unmatched"

    async def _revoke_access_for_payment(self, payment: Payment, *, reason: str) -> bool:
        """Ends the subscription a reversed payment was funding — but only if
        it is the payment that funds the CURRENT period. Reversing an older
        month is recorded, never allowed to cut off time the customer has
        legitimately paid for since. Returns whether access was revoked."""
        if payment.subscription_id is None:
            await self._record(
                payment.user_id, "access.revoke_skipped", payment.reference, {"reason": "no_subscription"}
            )
            await self.db.commit()
            return False

        latest = await self.repo.latest_paid_payment_id(payment.subscription_id)
        subscription = await self.repo.get_subscription(payment.subscription_id)
        if latest != payment.id or subscription is None or subscription.status != SubscriptionStatus.ACTIVE:
            await self._record(
                payment.user_id, "access.revoke_skipped", payment.reference,
                {"reason": "not_latest_payment" if latest != payment.id else "subscription_not_active",
                 "trigger": reason},
                subscription_id=payment.subscription_id,
            )
            await self.db.commit()
            return False

        codes = (
            (subscription.paystack_subscription_code, subscription.paystack_email_token)
            if subscription.auto_renew else None
        )
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.auto_renew = False
        if subscription.cancelled_at is None:
            subscription.cancelled_at = datetime.now(timezone.utc)
        await self._record(
            payment.user_id, "subscription.revoked", payment.reference, {"reason": reason},
            subscription_id=subscription.id,
        )
        await self.db.commit()

        # Post-commit, best effort — the access cut above must stand even if
        # Paystack is unreachable. The customer must still stop being billed.
        if codes and all(codes):
            try:
                await self.provider.disable_subscription(subscription_code=codes[0], email_token=codes[1])
            except Exception as exc:  # noqa: BLE001
                logger.error("revoked_subscription_disable_failed", subscription_id=str(subscription.id), error=str(exc))
                await self._record(
                    payment.user_id, "subscription.revoked_disable_failed", payment.reference,
                    {"error": str(exc)[:200]}, subscription_id=subscription.id,
                )
                await self.db.commit()

        if reason == "disputed":
            title, body = (
                "Your subscription has been paused",
                "A dispute was opened on your latest payment, so your Tour-Wayva subscription has been paused. "
                "If this was a mistake, please contact support.",
            )
        else:
            title, body = (
                "Your payment was refunded",
                "Your payment has been refunded and your Tour-Wayva subscription has ended.",
            )
        await self._notify(payment.user_id, title=title, body=body)
        return True

    async def _record(
        self,
        user_id: Optional[uuid.UUID],
        event_type: str,
        reference: Optional[str],
        details: dict[str, Any],
        *,
        subscription_id: Optional[uuid.UUID] = None,
    ) -> None:
        await self.repo.add_event(
            SubscriptionEvent(
                user_id=user_id, subscription_id=subscription_id, event_type=event_type,
                provider_reference=reference, details=details,
            )
        )
