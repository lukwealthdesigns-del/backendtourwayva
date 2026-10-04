"""MockPaymentProvider (Master Prompt §92) — an in-memory payment
processor for tests: no network, deterministic, inspectable."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.exceptions import PaymentProviderError
from app.providers.payments.interface import CheckoutSession, PaymentProvider, RefundResult, VerifiedTransaction
from app.providers.payments.paystack_parsing import signature_matches

MOCK_SECRET = "sk_test_mock_secret"


class MockPaymentProvider(PaymentProvider):
    def __init__(self, secret: str = MOCK_SECRET):
        self.secret = secret
        self.initialized: list[dict[str, Any]] = []
        self.transactions: dict[str, VerifiedTransaction] = {}
        self.disabled: list[tuple[str, str]] = []
        self.fail_disable = False
        self.refunds: list[dict[str, Any]] = []
        self.fail_refund = False

    async def initialize_transaction(
        self, *, email, amount_minor, currency, reference, callback_url, metadata, plan_code=None
    ) -> CheckoutSession:
        self.initialized.append(
            {"email": email, "amount_minor": amount_minor, "currency": currency, "reference": reference,
             "callback_url": callback_url, "metadata": metadata, "plan_code": plan_code}
        )
        return CheckoutSession(
            authorization_url=f"https://checkout.mock/{reference}", access_code=f"ac_{reference}", reference=reference
        )

    def set_transaction(self, reference: str, *, status: str = "success", amount_minor: int, currency: str,
                        customer_code: Optional[str] = "CUS_mock", plan_code: Optional[str] = None) -> None:
        self.transactions[reference] = VerifiedTransaction(
            reference=reference, status=status, amount_minor=amount_minor, currency=currency.upper(),
            paid_at=datetime.now(timezone.utc), gateway_response="Successful", customer_code=customer_code,
            plan_code=plan_code,
        )

    async def verify_transaction(self, reference: str) -> VerifiedTransaction:
        if reference not in self.transactions:
            raise PaymentProviderError("Transaction reference not found.")
        return self.transactions[reference]

    async def disable_subscription(self, *, subscription_code: str, email_token: str) -> None:
        if self.fail_disable:
            raise PaymentProviderError("disable failed")
        self.disabled.append((subscription_code, email_token))

    async def refund_transaction(
        self, *, reference: str, amount_minor: Optional[int] = None, reason: Optional[str] = None
    ) -> RefundResult:
        if self.fail_refund:
            raise PaymentProviderError("refund rejected")
        self.refunds.append({"reference": reference, "amount_minor": amount_minor, "reason": reason})
        return RefundResult(
            transaction_reference=reference, status="pending", amount_minor=amount_minor, currency=None,
            refund_id=f"rf_{len(self.refunds)}",
        )

    def verify_webhook_signature(self, *, raw_body: bytes, signature: Optional[str]) -> bool:
        return signature_matches(self.secret, raw_body, signature)
