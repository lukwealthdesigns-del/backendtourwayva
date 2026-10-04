"""
Payment endpoints (Paystack).

  POST /payments/checkout           start a payment for a plan
  POST /payments/verify             confirm a payment after the customer returns
  GET  /payments/me                 the caller's payment history
  POST /payments/webhook/paystack   Paystack -> us (signature-authenticated, IP-checked, no JWT)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit
from app.db.models.user import User
from app.db.session import get_db
from app.modules.payments.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PaymentResponse,
    VerifyPaymentRequest,
    WebhookAck,
)
from app.modules.payments.service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(rate_limit(bucket="payments:checkout", max_requests=10, window_seconds=3600, per="user"))],
)
async def create_checkout(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates a pending payment and returns the Paystack `authorization_url`
    to redirect the customer to. The amount and currency come from the plan,
    never from the request."""
    result = await PaymentService(db).create_checkout(current_user, payload.plan_id)
    return CheckoutResponse(
        authorization_url=result.authorization_url,
        reference=result.reference,
        access_code=result.access_code,
        public_key=result.public_key,
    )


@router.post(
    "/verify",
    response_model=PaymentResponse,
    dependencies=[Depends(rate_limit(bucket="payments:verify", max_requests=30, window_seconds=300, per="user"))],
)
async def verify_payment(
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Call this when the customer returns from Paystack. The payment is
    confirmed server-to-server; the subscription activates only if the
    confirmed amount and currency match. Idempotent — safe to call repeatedly,
    and safe alongside the webhook."""
    payment = await PaymentService(db).verify_and_apply(reference=payload.reference, user=current_user)
    return PaymentResponse.model_validate(payment)


@router.get("/me", response_model=list[PaymentResponse])
async def my_payments(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payments = await PaymentService(db).list_my_payments(current_user.id, limit=limit, offset=offset)
    return [PaymentResponse.model_validate(p) for p in payments]


@router.post("/webhook/paystack", response_model=WebhookAck)
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Paystack calls this. There is no JWT: the request is authenticated by
    the HMAC-SHA512 signature over the RAW body (`x-paystack-signature`). An
    invalid signature is rejected with 401 before anything is parsed. As defense
    in depth the source IP is also checked against PAYSTACK_WEBHOOK_IP_ALLOWLIST
    (403 when PAYSTACK_ENFORCE_WEBHOOK_IP_ALLOWLIST is on). Handles charge, subscription,
    refund and dispute events. Set this URL in the Paystack dashboard. Redelivered
    events are ignored."""
    raw_body = await request.body()
    result = await PaymentService(db).handle_webhook(
        raw_body=raw_body, signature=x_paystack_signature, client_ip=await get_client_ip(request)
    )
    return WebhookAck(result=result)
