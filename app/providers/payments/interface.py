"""Payment provider abstraction (Master Prompt §2 provider principle).

Business logic (PaymentService) depends only on this interface, never on
Paystack directly, so another processor can be added without touching it.
Amounts are ALWAYS integers in the currency's minor unit.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class CheckoutSession:
    authorization_url: str      # where to send the customer to pay
    access_code: str
    reference: str


@dataclass(frozen=True)
class VerifiedTransaction:
    """A transaction as reported by the provider (either from the verify
    API or from a signed webhook payload)."""

    reference: str
    status: str                       # provider status, lower-cased: success | failed | abandoned | ...
    amount_minor: int
    currency: str                     # upper-cased
    paid_at: Optional[datetime] = None
    gateway_response: Optional[str] = None
    customer_email: Optional[str] = None
    customer_code: Optional[str] = None
    plan_code: Optional[str] = None   # set when the charge belongs to a provider-managed plan
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RefundResult:
    """What the provider says about a refund it just accepted. Refunds are
    ASYNCHRONOUS at Paystack: `status` is normally "pending"/"processing"
    here, and the FINAL outcome arrives later as a signed webhook — so a
    RefundResult is never proof the money has moved."""

    transaction_reference: str
    status: str                        # lower-cased provider status
    amount_minor: Optional[int] = None
    currency: Optional[str] = None
    refund_id: Optional[str] = None


class PaymentProvider(ABC):
    @abstractmethod
    async def initialize_transaction(
        self,
        *,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        callback_url: Optional[str],
        metadata: dict[str, Any],
        plan_code: Optional[str] = None,
    ) -> CheckoutSession:
        raise NotImplementedError

    @abstractmethod
    async def verify_transaction(self, reference: str) -> VerifiedTransaction:
        raise NotImplementedError

    @abstractmethod
    async def disable_subscription(self, *, subscription_code: str, email_token: str) -> None:
        """Stop future renewals of a provider-managed subscription."""
        raise NotImplementedError

    @abstractmethod
    async def refund_transaction(
        self, *, reference: str, amount_minor: Optional[int] = None, reason: Optional[str] = None
    ) -> RefundResult:
        """Ask the provider to refund (part of) a settled transaction.
        `amount_minor=None` refunds the full remaining amount. Returns as
        soon as the provider has ACCEPTED the request; completion is
        reported by webhook."""
        raise NotImplementedError

    @abstractmethod
    def verify_webhook_signature(self, *, raw_body: bytes, signature: Optional[str]) -> bool:
        raise NotImplementedError
