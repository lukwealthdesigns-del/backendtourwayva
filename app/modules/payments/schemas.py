"""Schemas for checkout, payment verification and payment history."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from app.core.constants import PaymentStatus
from app.utils.money import from_minor_units


class CheckoutRequest(BaseModel):
    """The client chooses a plan — nothing else. Price and currency always
    come from the plan record on the server."""

    model_config = {"extra": "forbid"}

    plan_id: uuid.UUID


class CheckoutResponse(BaseModel):
    authorization_url: str = Field(..., description="Redirect the customer here to pay.")
    reference: str
    access_code: str
    public_key: Optional[str] = Field(default=None, description="Paystack public key for inline checkout, if enabled.")


class VerifyPaymentRequest(BaseModel):
    reference: str = Field(..., min_length=3, max_length=100, pattern=r"^[A-Za-z0-9.=-]+$")


class PaymentResponse(BaseModel):
    id: uuid.UUID
    reference: str
    plan_id: uuid.UUID
    subscription_id: Optional[uuid.UUID] = None
    amount_minor: int
    currency: str
    status: PaymentStatus
    is_renewal: bool
    refunded_amount_minor: int = 0
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount(self) -> float:
        """Display amount in major units (e.g. naira). Use amount_minor for arithmetic."""
        return from_minor_units(self.amount_minor)


class AdminPaymentResponse(PaymentResponse):
    user_id: uuid.UUID
    provider: str
    gateway_response: Optional[str] = None


class AdminPaymentListResponse(BaseModel):
    items: list[AdminPaymentResponse]
    total: int
    limit: int
    offset: int


class RefundRequest(BaseModel):
    """Admin request to refund a verified payment. `amount_minor` omitted =
    refund everything not yet refunded. `reason` is mandatory: it goes to the
    audit log and to Paystack's merchant note."""

    model_config = {"extra": "forbid"}

    amount_minor: Optional[int] = Field(
        default=None, gt=0, description="Amount to refund in MINOR units (kobo/cents). Omit for the full remaining amount."
    )
    reason: str = Field(..., min_length=5, max_length=250)


class RefundResponse(BaseModel):
    payment_id: uuid.UUID
    reference: str
    requested_amount_minor: int
    provider_status: str = Field(
        description="Paystack's status for the refund request, normally 'pending'. Refunds complete "
        "asynchronously: the payment only becomes 'refunded' once Paystack's refund.processed webhook arrives."
    )
    message: str


class WebhookAck(BaseModel):
    result: str
