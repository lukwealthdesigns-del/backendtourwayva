"""
Paystack payment flow tests — no network, no database. An in-memory fake
implements the repository methods the services call, and MockPaymentProvider
plays Paystack. Each test states the property it protects.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.constants import BillingInterval, PaymentStatus, SubscriptionStatus, UserRole, UserStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PaymentProviderError,
    ProviderUnavailableError,
    UnauthorizedError,
    ValidationAppError,
)
from app.db.models.monetization import Plan, Subscription
from app.db.models.user import User
from app.modules.payments.service import PaymentService
from app.modules.subscriptions.service import SubscriptionService
from app.providers.payments.mock_provider import MockPaymentProvider
from app.providers.payments.paystack_parsing import compute_signature

PRICE_MINOR = 500_000  # NGN 5,000.00


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------
class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def rollback(self):
        pass


class _FakeStore:
    """Implements PaymentRepository + MonetizationRepository methods."""

    def __init__(self):
        self.payments: dict[str, object] = {}
        self.events: list = []
        self.webhook_keys: set[str] = set()
        self.subs: list[Subscription] = []
        self.plans: dict[uuid.UUID, Plan] = {}

    # payments
    async def create_payment(self, payment):
        payment.id = payment.id or uuid.uuid4()
        payment.refunded_amount_minor = payment.refunded_amount_minor or 0   # column default, applied on flush in prod
        self.payments[payment.reference] = payment
        return payment

    async def get_by_id(self, payment_id, *, for_update=False):
        return next((p for p in self.payments.values() if p.id == payment_id), None)

    async def latest_paid_payment_id(self, subscription_id):
        paid = sorted(
            (p for p in self.payments.values() if p.subscription_id == subscription_id and p.paid_at is not None),
            key=lambda p: p.paid_at,
        )
        return paid[-1].id if paid else None

    async def get_subscription(self, subscription_id):
        return next((s for s in self.subs if s.id == subscription_id), None)

    async def refund_reference_seen(self, refund_reference):
        return any(
            e.event_type == "refund.applied" and (e.details or {}).get("refund_reference") == refund_reference
            for e in self.events
        )

    async def get_by_reference(self, reference, *, for_update=False):
        return self.payments.get(reference)

    async def add_event(self, event):
        self.events.append(event)
        return event

    async def list_stale_pending(self, *, older_than, limit):
        return [p for p in self.payments.values()
                if p.status == PaymentStatus.PENDING and p.created_at is not None and p.created_at <= older_than][:limit]

    async def register_webhook_event(self, event_key, event_type):
        if event_key in self.webhook_keys:
            return False
        self.webhook_keys.add(event_key)
        return True

    async def find_subscription_create_event(self, customer_code, plan_code):
        for e in reversed(self.events):
            d = e.details or {}
            if (e.event_type == "paystack.subscription.create" and d.get("customer_code") == customer_code
                    and d.get("plan_code") == plan_code):
                return e
        return None

    async def get_subscription_by_provider_code(self, code):
        return next((s for s in reversed(self.subs) if s.paystack_subscription_code == code), None)

    async def find_current_subscription(self, *, customer_code, plan_id):
        return next(
            (s for s in reversed(self.subs)
             if s.paystack_customer_code == customer_code and s.plan_id == plan_id
             and s.status == SubscriptionStatus.ACTIVE),
            None,
        )

    # monetization
    async def get_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def get_plan_by_paystack_code(self, code):
        return next((p for p in self.plans.values() if p.paystack_plan_code == code), None)

    async def get_active_subscription(self, user_id):
        now = datetime.now(timezone.utc)
        live = [s for s in self.subs if s.user_id == user_id and s.status == SubscriptionStatus.ACTIVE
                and s.current_period_end > now]
        return live[-1] if live else None

    async def create_subscription(self, subscription):
        subscription.id = subscription.id or uuid.uuid4()
        self.subs.append(subscription)
        return subscription

    async def save_subscription(self, subscription):
        return subscription


class _StubSecurity:
    events: list[dict] = []

    def __init__(self, db):
        pass

    async def record_event(self, **kwargs):
        _StubSecurity.events.append(kwargs)


class _StubAdmin:
    logs: list[dict] = []

    def __init__(self, db):
        pass

    async def log(self, **kwargs):
        _StubAdmin.logs.append(kwargs)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    _StubSecurity.events = []
    _StubAdmin.logs = []
    monkeypatch.setattr("app.modules.security.service.SecurityService", _StubSecurity)
    monkeypatch.setattr("app.modules.admin.admin_service.AdminService", _StubAdmin)
    monkeypatch.setattr(settings, "PAYSTACK_WEBHOOK_IP_ALLOWLIST", [])
    monkeypatch.setattr(settings, "PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST", False)
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "PAYSTACK_CALLBACK_URL", "https://app.example.com/billing/callback")


def _plan(*, price=5000.0, currency="NGN", code="PLN_pro", interval=BillingInterval.MONTHLY):
    return Plan(
        id=uuid.uuid4(), name="Pro", slug=f"pro-{uuid.uuid4().hex[:6]}", price_amount=price,
        price_currency=currency, billing_interval=interval, included_feature_flags=[],
        is_active=True, paystack_plan_code=code,
    )


def _user(email="ada@example.com"):
    return User(id=uuid.uuid4(), email=email, is_active=True, status=UserStatus.ACTIVE, role=UserRole.USER)


class _Harness:
    def __init__(self, plan=None):
        self.store = _FakeStore()
        self.provider = MockPaymentProvider()
        self.db = _FakeDB()
        self.plan = plan or _plan()
        self.store.plans[self.plan.id] = self.plan
        self.notifications: list[dict] = []

        subs = SubscriptionService.__new__(SubscriptionService)
        subs.db = self.db
        subs.repo = self.store
        subs.payment_repo = self.store
        subs._payment_provider = self.provider

        svc = PaymentService.__new__(PaymentService)
        svc.db = self.db
        svc.provider = self.provider
        svc.repo = self.store
        svc.monetization = self.store
        svc.subscriptions = subs

        async def _notify(user_id, *, title, body):
            self.notifications.append({"user_id": user_id, "title": title})

        svc._notify = _notify
        self.service = svc
        self.subscriptions = subs

    def run(self, coro):
        return asyncio.run(coro)

    def checkout(self, user, plan=None):
        return self.run(self.service.create_checkout(user, (plan or self.plan).id))

    def verify(self, user, reference):
        return self.run(self.service.verify_and_apply(reference=reference, user=user))

    def confirm_at_provider(self, reference, *, amount=PRICE_MINOR, currency="NGN", status="success",
                            customer="CUS_1", plan_code="PLN_pro"):
        self.provider.set_transaction(
            reference, status=status, amount_minor=amount, currency=currency,
            customer_code=customer, plan_code=plan_code,
        )

    def webhook(self, event: dict, *, signature=None, raw=None, client_ip=None):
        body = raw if raw is not None else json.dumps(event).encode()
        sig = signature if signature is not None else compute_signature(self.provider.secret, body)
        return self.run(self.service.handle_webhook(raw_body=body, signature=sig, client_ip=client_ip))


def _charge_success_event(reference, *, amount=PRICE_MINOR, currency="NGN", customer="CUS_1", plan_code="PLN_pro"):
    return {
        "event": "charge.success",
        "data": {
            "reference": reference, "status": "success", "amount": amount, "currency": currency,
            "paid_at": "2026-09-20T10:00:00.000Z", "gateway_response": "Successful",
            "customer": {"email": "ada@example.com", "customer_code": customer},
            "plan": {"plan_code": plan_code} if plan_code else {},
        },
    }


def _subscription_create_event(code="SUB_1", token="tok_1", customer="CUS_1", plan_code="PLN_pro"):
    return {
        "event": "subscription.create",
        "data": {"subscription_code": code, "email_token": token,
                 "customer": {"customer_code": customer}, "plan": {"plan_code": plan_code}},
    }


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def test_checkout_uses_server_side_price_and_a_valid_paystack_reference():
    h, user = _Harness(), _user()
    result = h.checkout(user)

    assert re.fullmatch(r"TW-[0-9a-f]{32}", result.reference)        # only chars Paystack accepts
    sent = h.provider.initialized[0]
    assert sent["amount_minor"] == PRICE_MINOR and sent["currency"] == "NGN"
    assert sent["email"] == user.email and sent["plan_code"] == "PLN_pro"
    assert sent["metadata"]["user_id"] == str(user.id)
    payment = h.store.payments[result.reference]
    assert payment.status == PaymentStatus.PENDING and payment.amount_minor == PRICE_MINOR
    assert h.db.commits >= 1  # committed before the provider call


def test_checkout_rejects_free_plan_unsupported_currency_and_missing_config(monkeypatch):
    free = _plan(price=0.0, code=None, interval=BillingInterval.FREE)
    h = _Harness(free)
    with pytest.raises(ValidationAppError):
        h.checkout(_user())

    weird = _Harness(_plan(currency="EUR"))
    with pytest.raises(ValidationAppError):
        weird.checkout(_user())

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "")
    with pytest.raises(ProviderUnavailableError):
        _Harness().checkout(_user())


def test_checkout_unknown_plan_is_not_found():
    h = _Harness()
    with pytest.raises(NotFoundError):
        h.run(h.service.create_checkout(_user(), uuid.uuid4()))


def test_failed_initialize_marks_payment_failed_and_reraises():
    h, user = _Harness(), _user()

    async def boom(**kwargs):
        raise PaymentProviderError("nope")

    h.provider.initialize_transaction = boom
    with pytest.raises(PaymentProviderError):
        h.checkout(user)
    (payment,) = h.store.payments.values()
    assert payment.status == PaymentStatus.FAILED


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def test_verified_payment_activates_subscription_once():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    h.confirm_at_provider(reference)

    payment = h.verify(user, reference)

    assert payment.status == PaymentStatus.SUCCESS and payment.subscription_id is not None
    (sub,) = h.store.subs
    assert sub.status == SubscriptionStatus.ACTIVE and sub.auto_renew is True
    assert sub.paystack_customer_code == "CUS_1"
    assert timedelta(days=29) < sub.current_period_end - datetime.now(timezone.utc) <= timedelta(days=30)
    assert "payment.succeeded" in [e.event_type for e in h.store.events]
    assert h.notifications and h.notifications[0]["title"] == "Payment received"

    h.verify(user, reference)                     # idempotent: calling again changes nothing
    assert len(h.store.subs) == 1


def test_amount_or_currency_mismatch_never_activates_and_raises_a_critical_alert():
    for kwargs in ({"amount": 100}, {"currency": "USD"}):
        h, user = _Harness(), _user()
        reference = h.checkout(user).reference
        h.confirm_at_provider(reference, **kwargs)

        payment = h.verify(user, reference)

        assert payment.status == PaymentStatus.FAILED
        assert h.store.subs == []
        assert [e["event_type"] for e in _StubSecurity.events][-1] == "payment_amount_mismatch"
        assert "payment.amount_mismatch" in [e.event_type for e in h.store.events]


def test_verify_someone_elses_or_unknown_payment_is_not_found():
    h, owner, other = _Harness(), _user(), _user("eve@example.com")
    reference = h.checkout(owner).reference
    h.confirm_at_provider(reference)
    with pytest.raises(NotFoundError):
        h.verify(other, reference)
    with pytest.raises(NotFoundError):
        h.verify(owner, "TW-doesnotexist")
    assert h.store.subs == []


def test_abandoned_and_failed_transactions_do_not_activate():
    for provider_status, expected in (("abandoned", PaymentStatus.ABANDONED), ("failed", PaymentStatus.FAILED)):
        h, user = _Harness(), _user()
        reference = h.checkout(user).reference
        h.confirm_at_provider(reference, status=provider_status)
        assert h.verify(user, reference).status == expected
        assert h.store.subs == []


def test_pending_transaction_stays_pending():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    h.confirm_at_provider(reference, status="ongoing")
    assert h.verify(user, reference).status == PaymentStatus.PENDING
    assert h.store.subs == []


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def test_webhook_with_bad_or_missing_signature_is_rejected_before_any_processing():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    event = _charge_success_event(reference)
    with pytest.raises(UnauthorizedError):
        h.webhook(event, signature="0" * 128)
    with pytest.raises(UnauthorizedError):
        h.run(h.service.handle_webhook(raw_body=json.dumps(event).encode(), signature=None))
    assert h.store.subs == [] and h.store.webhook_keys == set()


def test_charge_success_webhook_activates_and_redelivery_is_a_noop():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    event = _charge_success_event(reference)

    assert h.webhook(event) == "processed"
    assert len(h.store.subs) == 1
    assert h.webhook(event) == "duplicate"        # same body delivered again
    assert len(h.store.subs) == 1


def test_webhook_then_verify_race_activates_exactly_once():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    h.confirm_at_provider(reference)

    h.webhook(_charge_success_event(reference))
    h.verify(user, reference)                     # user's redirect arrives second
    assert len(h.store.subs) == 1


def test_webhook_amount_mismatch_is_rejected():
    h, user = _Harness(), _user()
    reference = h.checkout(user).reference
    h.webhook(_charge_success_event(reference, amount=1))
    assert h.store.subs == []
    assert h.store.payments[reference].status == PaymentStatus.FAILED


def test_unknown_event_types_are_acknowledged_and_ignored():
    h = _Harness()
    assert h.webhook({"event": "transfer.success", "data": {}}) == "ignored"
    assert h.webhook({"event": "transfer.success", "data": {}}) == "duplicate"


def test_malformed_webhook_body_is_a_validation_error():
    h = _Harness()
    body = b"not json"
    with pytest.raises(ValidationAppError):
        h.run(h.service.handle_webhook(raw_body=body, signature=compute_signature(h.provider.secret, body)))


# ---------------------------------------------------------------------------
# Recurring billing
# ---------------------------------------------------------------------------
def _activated(h, user, reference_customer="CUS_1"):
    reference = h.checkout(user).reference
    h.webhook(_charge_success_event(reference, customer=reference_customer))
    return h.store.subs[-1]


def test_renewal_charge_extends_the_period_and_records_a_renewal_payment():
    h, user = _Harness(), _user()
    sub = _activated(h, user)
    old_end = sub.current_period_end

    assert h.webhook(_charge_success_event("PSK-renewal-1")) == "processed"

    assert sub.current_period_end == old_end + timedelta(days=30)
    renewal = h.store.payments["PSK-renewal-1"]
    assert renewal.is_renewal and renewal.status == PaymentStatus.SUCCESS and renewal.user_id == user.id
    assert len(h.store.subs) == 1                 # extended, not replaced


def test_renewal_with_wrong_amount_is_rejected_and_does_not_extend():
    h, user = _Harness(), _user()
    sub = _activated(h, user)
    old_end = sub.current_period_end
    assert h.webhook(_charge_success_event("PSK-renewal-2", amount=999)) == "rejected"
    assert sub.current_period_end == old_end and "PSK-renewal-2" not in h.store.payments
    assert _StubSecurity.events[-1]["event_type"] == "renewal_amount_mismatch"


def test_renewal_for_unknown_customer_is_recorded_not_applied():
    h = _Harness()
    assert h.webhook(_charge_success_event("PSK-x", customer="CUS_nobody")) == "unmatched"
    assert h.store.subs == [] and "PSK-x" not in h.store.payments


def test_subscription_codes_attach_when_subscription_create_arrives_after_the_charge():
    h, user = _Harness(), _user()
    sub = _activated(h, user)
    h.webhook(_subscription_create_event())
    assert (sub.paystack_subscription_code, sub.paystack_email_token) == ("SUB_1", "tok_1")


def test_subscription_codes_attach_when_subscription_create_arrives_before_the_charge():
    h, user = _Harness(), _user()
    h.webhook(_subscription_create_event())                       # parked: no subscription yet
    parked = h.store.events[-1]
    assert parked.details["email_token"] == "tok_1" and parked.details["attached"] is False

    sub = _activated(h, user)

    assert (sub.paystack_subscription_code, sub.paystack_email_token) == ("SUB_1", "tok_1")
    assert parked.details["email_token"] is None and parked.details["attached"] is True  # token not kept around


def test_provider_disable_or_not_renew_stops_auto_renew_but_keeps_access():
    for event_name in ("subscription.disable", "subscription.not_renew"):
        h, user = _Harness(), _user()
        sub = _activated(h, user)
        h.webhook(_subscription_create_event())
        end = sub.current_period_end

        h.webhook({"event": event_name, "data": {"subscription_code": "SUB_1"}})

        assert sub.auto_renew is False and sub.cancelled_at is not None
        assert sub.status == SubscriptionStatus.ACTIVE and sub.current_period_end == end


def test_failed_invoice_notifies_the_user():
    h, user = _Harness(), _user()
    _activated(h, user)
    h.webhook(_subscription_create_event())
    h.notifications.clear()
    h.webhook({"event": "invoice.payment_failed", "data": {"subscription": {"subscription_code": "SUB_1"}}})
    assert h.notifications and "renew" in h.notifications[0]["title"]


def test_buying_a_new_plan_disables_the_previous_auto_renewing_subscription():
    h, user = _Harness(), _user()
    _activated(h, user)
    h.webhook(_subscription_create_event(code="SUB_old", token="tok_old"))

    other = _plan(price=12000.0, code="PLN_other", interval=BillingInterval.YEARLY)
    h.store.plans[other.id] = other
    reference = h.checkout(user, other).reference
    h.webhook(_charge_success_event(reference, amount=1_200_000, plan_code="PLN_other", customer="CUS_1"))

    assert h.provider.disabled == [("SUB_old", "tok_old")]
    live = [s for s in h.store.subs if s.status == SubscriptionStatus.ACTIVE]
    assert len(live) == 1 and live[0].plan_id == other.id


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
def test_cancel_paid_subscription_disables_at_provider_and_keeps_access():
    h, user = _Harness(), _user()
    sub = _activated(h, user)
    h.webhook(_subscription_create_event())
    end = sub.current_period_end

    result = h.run(h.subscriptions.cancel_my_subscription(user.id))

    assert h.provider.disabled == [("SUB_1", "tok_1")]
    assert result.auto_renew is False and result.cancelled_at is not None
    assert result.status == SubscriptionStatus.ACTIVE and result.current_period_end == end
    assert h.run(h.subscriptions.cancel_my_subscription(user.id)) is result   # idempotent


def test_cancel_does_not_mark_cancelled_if_the_provider_call_fails():
    h, user = _Harness(), _user()
    sub = _activated(h, user)
    h.webhook(_subscription_create_event())
    h.provider.fail_disable = True

    with pytest.raises(PaymentProviderError):
        h.run(h.subscriptions.cancel_my_subscription(user.id))
    assert sub.auto_renew is True and sub.cancelled_at is None   # still billed => still shown active


def test_cancel_before_provider_codes_exist_asks_the_user_to_retry():
    h, user = _Harness(), _user()
    _activated(h, user)                                           # subscription.create not received yet
    with pytest.raises(ConflictError):
        h.run(h.subscriptions.cancel_my_subscription(user.id))


def test_cancel_free_plan_ends_immediately():
    free = _plan(price=0.0, code=None, interval=BillingInterval.FREE)
    h, user = _Harness(free), _user()
    sub = h.run(h.subscriptions.subscribe(user_id=user.id, plan_id=free.id))
    cancelled = h.run(h.subscriptions.cancel_my_subscription(user.id))
    assert cancelled is sub and cancelled.status == SubscriptionStatus.CANCELLED


# ---------------------------------------------------------------------------
# Scheduled reconciliation (webhook never arrived)
# ---------------------------------------------------------------------------
def _pending(h, user, age):
    reference = h.checkout(user).reference
    h.store.payments[reference].created_at = datetime.now(timezone.utc) - age
    return reference


def test_a_payment_whose_webhook_was_lost_is_completed_by_the_reconciler():
    h, user = _Harness(), _user()
    reference = _pending(h, user, timedelta(minutes=45))
    h.confirm_at_provider(reference)                                   # the customer DID pay

    counts = h.run(h.service.reconcile_pending())

    assert counts == {"checked": 1, "applied": 1, "abandoned": 0, "errors": 0}
    assert h.store.payments[reference].status == PaymentStatus.SUCCESS and len(h.store.subs) == 1


def test_recent_payments_are_left_alone_so_the_normal_flow_can_finish():
    h, user = _Harness(), _user()
    reference = _pending(h, user, timedelta(minutes=2))
    h.confirm_at_provider(reference)
    assert h.run(h.service.reconcile_pending())["checked"] == 0
    assert h.store.payments[reference].status == PaymentStatus.PENDING and h.store.subs == []


def test_the_reconciler_applies_the_same_safety_checks_as_the_normal_path():
    h, user = _Harness(), _user()
    reference = _pending(h, user, timedelta(minutes=45))
    h.confirm_at_provider(reference, amount=100)                        # wrong amount
    counts = h.run(h.service.reconcile_pending())
    assert counts["applied"] == 0 and h.store.payments[reference].status == PaymentStatus.FAILED and h.store.subs == []


def test_unpaid_checkouts_are_abandoned_by_the_provider_or_by_timeout():
    h, user = _Harness(), _user()
    left = _pending(h, user, timedelta(minutes=45))
    h.confirm_at_provider(left, status="abandoned")
    stale = _pending(h, user, timedelta(hours=60))
    h.confirm_at_provider(stale, status="ongoing")                      # provider still says "in progress" after 60h
    fresh = _pending(h, user, timedelta(hours=1))
    h.confirm_at_provider(fresh, status="ongoing")

    counts = h.run(h.service.reconcile_pending())

    assert h.store.payments[left].status == PaymentStatus.ABANDONED
    assert h.store.payments[stale].status == PaymentStatus.ABANDONED and h.store.payments[stale].gateway_response == "timed_out"
    assert h.store.payments[fresh].status == PaymentStatus.PENDING
    assert counts["checked"] == 3 and counts["abandoned"] == 1 and h.store.subs == []


def test_one_broken_payment_does_not_stop_the_sweep():
    h, user = _Harness(), _user()
    unknown = _pending(h, user, timedelta(minutes=45))                  # the provider has never heard of it
    good = _pending(h, user, timedelta(minutes=50))
    h.confirm_at_provider(good)

    counts = h.run(h.service.reconcile_pending())

    assert counts["errors"] == 1 and counts["applied"] == 1
    assert h.store.payments[unknown].status == PaymentStatus.PENDING and h.store.payments[good].status == PaymentStatus.SUCCESS


# ---------------------------------------------------------------------------
# Webhook source IP allow-list (defense in depth on top of the HMAC signature)
# ---------------------------------------------------------------------------
PAYSTACK_IP = "52.31.139.75"


def test_unlisted_ip_is_processed_but_flagged_when_not_enforcing(monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_WEBHOOK_IP_ALLOWLIST", [PAYSTACK_IP])
    h = _Harness()

    assert h.webhook({"event": "ping", "data": {}}, client_ip="9.9.9.9") == "ignored"

    (event,) = _StubSecurity.events
    assert event["event_type"] == "paystack_webhook_unlisted_ip" and event["ip_address"] == "9.9.9.9"
    assert event["metadata"] == {"enforced": False}


def test_unlisted_ip_is_rejected_when_enforcing_and_nothing_is_recorded_as_processed(monkeypatch):
    from app.core.exceptions import ForbiddenError

    monkeypatch.setattr(settings, "PAYSTACK_WEBHOOK_IP_ALLOWLIST", [PAYSTACK_IP])
    monkeypatch.setattr(settings, "PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST", True)
    h = _Harness()

    with pytest.raises(ForbiddenError):
        h.webhook({"event": "ping", "data": {}}, client_ip="9.9.9.9")
    with pytest.raises(ForbiddenError):                       # an unknown source IP fails closed too
        h.webhook({"event": "ping", "data": {}}, client_ip=None)
    assert h.store.webhook_keys == set()                      # rejected before the de-dup record was written


def test_a_listed_ip_passes_without_a_security_event_even_when_enforcing(monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_WEBHOOK_IP_ALLOWLIST", [PAYSTACK_IP])
    monkeypatch.setattr(settings, "PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST", True)
    h = _Harness()

    assert h.webhook({"event": "ping", "data": {}}, client_ip=PAYSTACK_IP) == "ignored"
    assert _StubSecurity.events == []


def test_an_empty_allowlist_disables_the_ip_check(monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST", True)   # enforce flag alone does nothing
    h = _Harness()

    assert h.webhook({"event": "ping", "data": {}}, client_ip="9.9.9.9") == "ignored"


def test_the_signature_is_still_checked_first_so_scanners_generate_no_security_events(monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_WEBHOOK_IP_ALLOWLIST", [PAYSTACK_IP])
    h = _Harness()

    with pytest.raises(UnauthorizedError):
        h.webhook({"event": "ping", "data": {}}, signature="bad", client_ip="9.9.9.9")
    assert _StubSecurity.events == []


# ---------------------------------------------------------------------------
# Refunds and chargebacks
# ---------------------------------------------------------------------------
def _paid(h, user):
    """A verified payment funding an auto-renewing subscription whose Paystack
    codes are attached (so revoking it must also disable it at the provider)."""
    h.webhook(_subscription_create_event())
    reference = h.checkout(user).reference
    h.confirm_at_provider(reference)
    return h.verify(user, reference)


def _refund_event(reference, *, amount=PRICE_MINOR, refund_reference="RF_1", currency="NGN",
                  event="refund.processed", **extra):
    return {"event": event, "data": {"status": "processed", "transaction_reference": reference,
                                     "refund_reference": refund_reference, "amount": amount,
                                     "currency": currency, **extra}}


def _dispute_event(reference, *, event="charge.dispute.create", dispute_id=42, **extra):
    return {"event": event, "data": {"id": dispute_id, "status": "awaiting-merchant-feedback",
                                     "category": "chargeback", "transaction": {"reference": reference}, **extra}}


def _event_types(h):
    return [e.event_type for e in h.store.events]


def test_admin_refund_asks_the_provider_and_leaves_the_payment_untouched_until_the_webhook():
    h, user, admin_id = _Harness(), _user(), uuid.uuid4()
    payment = _paid(h, user)

    result = h.run(h.service.refund_payment(
        payment_id=payment.id, actor_id=admin_id, amount_minor=None, reason="Customer asked", ip_address="10.0.0.1"))

    assert h.provider.refunds == [{"reference": payment.reference, "amount_minor": PRICE_MINOR, "reason": "Customer asked"}]
    assert result.provider_status == "pending" and result.requested_amount_minor == PRICE_MINOR
    assert payment.status == PaymentStatus.SUCCESS and payment.refunded_amount_minor == 0     # refunds are async
    assert h.store.subs[0].status == SubscriptionStatus.ACTIVE
    assert "refund.initiated" in _event_types(h)
    (audit,) = _StubAdmin.logs
    assert audit["action"] == "payment.refund" and audit["admin_user_id"] == admin_id and audit["ip_address"] == "10.0.0.1"


def test_admin_partial_refund_is_capped_at_the_remaining_refundable_amount():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    payment.refunded_amount_minor = 200_000

    h.run(h.service.refund_payment(payment_id=payment.id, actor_id=uuid.uuid4(), amount_minor=300_000, reason="Goodwill"))
    assert h.provider.refunds[0]["amount_minor"] == 300_000            # exactly what is left

    with pytest.raises(ValidationAppError) as excinfo:
        h.run(h.service.refund_payment(payment_id=payment.id, actor_id=uuid.uuid4(), amount_minor=300_001, reason="Too much"))
    assert excinfo.value.details["remaining_refundable_minor"] == 300_000
    assert len(h.provider.refunds) == 1                                # the rejected request never reached Paystack


def test_only_successful_payments_that_exist_can_be_refunded():
    h, user = _Harness(), _user()
    pending = h.store.payments[h.checkout(user).reference]
    with pytest.raises(ConflictError):
        h.run(h.service.refund_payment(payment_id=pending.id, actor_id=uuid.uuid4(), amount_minor=None, reason="Nope"))
    with pytest.raises(NotFoundError):
        h.run(h.service.refund_payment(payment_id=uuid.uuid4(), actor_id=uuid.uuid4(), amount_minor=None, reason="Nope"))

    paid = _paid(h, user)
    paid.refunded_amount_minor = paid.amount_minor
    with pytest.raises(ConflictError):
        h.run(h.service.refund_payment(payment_id=paid.id, actor_id=uuid.uuid4(), amount_minor=None, reason="Again"))
    assert h.provider.refunds == []


def test_a_provider_failure_is_audited_and_recorded_nowhere_else():
    h, user, admin_id = _Harness(), _user(), uuid.uuid4()
    payment = _paid(h, user)
    h.provider.fail_refund = True

    with pytest.raises(PaymentProviderError):
        h.run(h.service.refund_payment(payment_id=payment.id, actor_id=admin_id, amount_minor=None, reason="Try"))

    (audit,) = _StubAdmin.logs
    assert audit["result"].value == "failure" and "refund rejected" in audit["metadata"]["error"]
    assert "refund.initiated" not in _event_types(h)


def test_a_processed_full_refund_marks_the_payment_and_ends_the_subscription():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    sub = h.store.subs[0]

    assert h.webhook(_refund_event(payment.reference)) == "processed"

    assert payment.status == PaymentStatus.REFUNDED and payment.refunded_amount_minor == PRICE_MINOR
    assert payment.refunded_at is not None
    assert sub.status == SubscriptionStatus.CANCELLED and sub.auto_renew is False and sub.cancelled_at is not None
    assert h.provider.disabled == [("SUB_1", "tok_1")]                 # the customer stops being billed at Paystack too
    assert {"refund.applied", "subscription.revoked"} <= set(_event_types(h))
    assert h.notifications[-1]["title"] == "Your payment was refunded"


def test_a_processed_partial_refund_records_the_amount_but_keeps_access():
    h, user = _Harness(), _user()
    payment = _paid(h, user)

    assert h.webhook(_refund_event(payment.reference, amount=100_000)) == "processed"

    assert payment.status == PaymentStatus.SUCCESS and payment.refunded_amount_minor == 100_000
    assert h.store.subs[0].status == SubscriptionStatus.ACTIVE and h.provider.disabled == []
    assert h.notifications[-1]["title"] == "Refund processed"

    assert h.webhook(_refund_event(payment.reference, amount=400_000, refund_reference="RF_2")) == "processed"
    assert payment.status == PaymentStatus.REFUNDED and h.store.subs[0].status == SubscriptionStatus.CANCELLED


def test_a_resent_refund_with_a_different_body_is_not_applied_twice():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    h.webhook(_refund_event(payment.reference, amount=100_000))

    assert h.webhook(_refund_event(payment.reference, amount=100_000, note="resent")) == "duplicate"
    assert payment.refunded_amount_minor == 100_000


def test_a_refund_that_cannot_be_matched_is_parked_for_reconciliation_not_guessed():
    h, user = _Harness(), _user()
    payment = _paid(h, user)

    assert h.webhook(_refund_event("TW-does-not-exist")) == "unmatched"
    assert h.webhook(_refund_event(payment.reference, amount=None, refund_reference="RF_2")) == "unmatched"

    assert payment.status == PaymentStatus.SUCCESS and payment.refunded_amount_minor == 0
    assert _event_types(h).count("refund.unmatched") == 2


def test_a_refund_in_a_different_currency_is_rejected_and_flagged():
    h, user = _Harness(), _user()
    payment = _paid(h, user)

    assert h.webhook(_refund_event(payment.reference, currency="USD")) == "rejected"

    assert payment.status == PaymentStatus.SUCCESS and payment.refunded_amount_minor == 0
    assert _StubSecurity.events[-1]["event_type"] == "refund_currency_mismatch"


def test_refunding_an_old_payment_does_not_cut_off_the_period_paid_for_since():
    h, user = _Harness(), _user()
    first = _paid(h, user)
    h.webhook(_charge_success_event("TW-renewal-1"))                   # a later renewal funds the current period
    renewal = h.store.payments["TW-renewal-1"]
    first.paid_at = datetime.now(timezone.utc) - timedelta(days=40)
    renewal.paid_at = datetime.now(timezone.utc)

    assert h.webhook(_refund_event(first.reference)) == "processed"

    assert first.status == PaymentStatus.REFUNDED
    assert h.store.subs[0].status == SubscriptionStatus.ACTIVE and h.provider.disabled == []
    skipped = [e for e in h.store.events if e.event_type == "access.revoke_skipped"]
    assert skipped and skipped[0].details["reason"] == "not_latest_payment"


def test_a_failed_refund_is_recorded_and_changes_nothing():
    h, user = _Harness(), _user()
    payment = _paid(h, user)

    assert h.webhook(_refund_event(payment.reference, event="refund.failed")) == "processed"

    assert payment.status == PaymentStatus.SUCCESS and payment.refunded_amount_minor == 0
    assert "refund.failed" in _event_types(h)


def test_a_chargeback_marks_the_payment_disputed_and_revokes_access_immediately():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    sub = h.store.subs[0]

    assert h.webhook(_dispute_event(payment.reference)) == "processed"

    assert payment.status == PaymentStatus.DISPUTED
    assert sub.status == SubscriptionStatus.CANCELLED and sub.auto_renew is False
    assert h.provider.disabled == [("SUB_1", "tok_1")]
    assert {"payment.disputed", "subscription.revoked"} <= set(_event_types(h))
    assert _StubSecurity.events[-1]["event_type"] == "payment_disputed"
    assert _StubSecurity.events[-1]["severity"].value == "critical"
    assert h.notifications[-1]["title"] == "Your subscription has been paused"


def test_resolving_a_dispute_is_recorded_but_never_restores_access_automatically():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    h.webhook(_dispute_event(payment.reference))

    result = h.webhook(_dispute_event(payment.reference, event="charge.dispute.resolve",
                                      status="resolved", resolution="merchant-accepted"))

    assert result == "processed"
    resolved = [e for e in h.store.events if e.event_type == "dispute.resolved"]
    assert resolved and resolved[0].details["resolution"] == "merchant-accepted"
    assert payment.status == PaymentStatus.DISPUTED and h.store.subs[0].status == SubscriptionStatus.CANCELLED


def test_a_dispute_for_an_unknown_payment_raises_a_security_event():
    h = _Harness()

    assert h.webhook(_dispute_event("TW-unknown")) == "unmatched"

    assert _StubSecurity.events[-1]["event_type"] == "payment_dispute_unmatched"
    assert "dispute.unmatched" in _event_types(h)


def test_a_dispute_on_an_already_refunded_payment_is_recorded_without_touching_access():
    h, user = _Harness(), _user()
    payment = _paid(h, user)
    h.webhook(_refund_event(payment.reference))                        # fully refunded, subscription already ended

    assert h.webhook(_dispute_event(payment.reference)) == "processed"

    assert payment.status == PaymentStatus.REFUNDED
    assert "dispute.on_refunded_payment" in _event_types(h)
