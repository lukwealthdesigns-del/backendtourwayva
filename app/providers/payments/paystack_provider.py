"""
Paystack implementation of PaymentProvider (https://paystack.com/docs/api).

  * initialize  -> POST /transaction/initialize
  * verify      -> GET  /transaction/verify/:reference   (retried: idempotent)
  * disable sub -> POST /subscription/disable  {code, token}
  * refund      -> POST /refund  {transaction, amount?, merchant_note?}  (async: final state via webhook)
  * webhooks    -> HMAC-SHA512 of the raw body, header `x-paystack-signature`

The secret key only ever lives in server-side settings. A request is never
retried unless it is idempotent: initialize uses a unique reference, so a
duplicate is rejected by Paystack rather than double-charged, but we still
do not blindly retry it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.exceptions import PaymentProviderError, ProviderUnavailableError
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError, get_breaker
from app.core.logging import get_logger
from app.providers.payments.interface import CheckoutSession, PaymentProvider, RefundResult, VerifiedTransaction
from app.providers.payments.paystack_parsing import parse_refund_response, parse_transaction, signature_matches

logger = get_logger(__name__)

_RETRY_BACKOFF_SECONDS = (0.5, 1.0)


class PaystackProvider(PaymentProvider):
    def __init__(
        self,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.secret_key = secret_key if secret_key is not None else settings.PAYSTACK_SECRET_KEY
        self.base_url = (base_url or settings.PAYSTACK_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.PAYSTACK_TIMEOUT_SECONDS

    async def _request(
        self, method: str, path: str, *, json: Optional[dict[str, Any]] = None, retries: int = 0
    ) -> dict[str, Any]:
        if not self.secret_key:
            raise ProviderUnavailableError("Payments are not configured.")

        # A dedicated circuit breaker on top of Paystack's own bounded retry above: if Paystack
        # is down hard, later calls fail in microseconds instead of waiting out the full retry
        # loop again (still bounded, but every checkout would otherwise pay that latency).
        breaker = get_breaker("paystack")
        if not breaker.allow():
            raise CircuitOpenError("paystack", breaker.retry_in())

        headers = {"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json"}
        response: Optional[httpx.Response] = None
        for attempt in range(retries + 1):
            is_last = attempt == retries
            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    response = await client.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as exc:
                if is_last:
                    breaker.record_failure()
                    logger.error("paystack_request_failed", path=path, error=redact_secrets(exc))
                    raise ProviderUnavailableError("Payment provider is temporarily unavailable.") from exc
            else:
                if response.status_code < 500 or is_last:
                    if response.status_code >= 500:
                        breaker.record_failure()
                    else:
                        breaker.record_success()
                    break
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)])

        assert response is not None
        try:
            body = response.json()
        except ValueError as exc:
            raise PaymentProviderError("Payment provider returned an unreadable response.") from exc

        if response.status_code >= 400 or not body.get("status"):
            logger.warning("paystack_request_rejected", path=path, http_status=response.status_code)
            raise PaymentProviderError(
                body.get("message") or "Payment provider rejected the request.",
                details={"http_status": response.status_code},
            )
        return body

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
        payload: dict[str, Any] = {
            "email": email,
            "amount": amount_minor,
            "currency": currency,
            "reference": reference,
            "metadata": metadata,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if plan_code:
            payload["plan"] = plan_code  # makes Paystack create a recurring subscription

        data = (await self._request("POST", "/transaction/initialize", json=payload))["data"]
        return CheckoutSession(
            authorization_url=data["authorization_url"],
            access_code=data["access_code"],
            reference=data["reference"],
        )

    async def verify_transaction(self, reference: str) -> VerifiedTransaction:
        body = await self._request("GET", f"/transaction/verify/{quote(reference, safe='')}", retries=2)
        return parse_transaction(body["data"])

    async def disable_subscription(self, *, subscription_code: str, email_token: str) -> None:
        await self._request(
            "POST", "/subscription/disable", json={"code": subscription_code, "token": email_token}
        )

    async def refund_transaction(
        self, *, reference: str, amount_minor: Optional[int] = None, reason: Optional[str] = None
    ) -> RefundResult:
        # Deliberately NOT retried (retries=0): a refund moves money out, and a
        # timed-out request may already have been accepted — blindly resending
        # it could refund twice. The caller surfaces the failure instead and an
        # admin can check the Paystack dashboard before trying again.
        payload: dict[str, Any] = {"transaction": reference}
        if amount_minor is not None:
            payload["amount"] = amount_minor
        if reason:
            payload["merchant_note"] = reason[:250]
        body = await self._request("POST", "/refund", json=payload, retries=0)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        return parse_refund_response(reference, data)

    def verify_webhook_signature(self, *, raw_body: bytes, signature: Optional[str]) -> bool:
        return signature_matches(self.secret_key, raw_body, signature)
