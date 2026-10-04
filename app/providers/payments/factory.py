from __future__ import annotations

from app.providers.payments.interface import PaymentProvider
from app.providers.payments.paystack_provider import PaystackProvider


def get_payment_provider() -> PaymentProvider:
    return PaystackProvider()
