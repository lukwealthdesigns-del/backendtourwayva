"""Pure (stdlib-only) helpers for Paystack payloads and webhook signatures,
kept separate from the HTTP client so they are trivially unit-testable."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any, Optional

from app.providers.payments.interface import RefundResult, VerifiedTransaction


def compute_signature(secret: str, raw_body: bytes) -> str:
    """Paystack signs the RAW request body with HMAC-SHA512 using your
    secret key and sends the hex digest in `x-paystack-signature`."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()


def signature_matches(secret: str, raw_body: bytes, header_value: Optional[str]) -> bool:
    if not secret or not header_value:
        return False
    return hmac.compare_digest(compute_signature(secret, raw_body), header_value.strip().lower())


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_transaction(data: dict[str, Any]) -> VerifiedTransaction:
    """Normalize a Paystack transaction object (verify API response `data`
    or webhook `charge.success` `data`)."""
    plan = data.get("plan_object") if isinstance(data.get("plan_object"), dict) else data.get("plan")
    if isinstance(plan, dict):
        plan_code = plan.get("plan_code") or None
    elif isinstance(plan, str) and plan:
        plan_code = plan
    else:
        plan_code = None

    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    try:
        amount_minor = int(data.get("amount", 0))
    except (TypeError, ValueError):
        amount_minor = 0

    return VerifiedTransaction(
        reference=str(data.get("reference", "")),
        status=str(data.get("status", "")).lower(),
        amount_minor=amount_minor,
        currency=str(data.get("currency", "")).upper(),
        paid_at=_parse_datetime(data.get("paid_at") or data.get("paidAt")),
        gateway_response=data.get("gateway_response"),
        customer_email=customer.get("email"),
        customer_code=customer.get("customer_code"),
        plan_code=plan_code,
        metadata=_as_dict(data.get("metadata")),
    )


def extract_transaction_reference(data: dict[str, Any]) -> str:
    """The originating transaction's reference from a refund.* or
    charge.dispute.* webhook `data` object. Paystack nests it differently per
    event family (`transaction_reference` on refunds, `transaction.reference`
    on disputes), so every known location is tried, in order. Empty string
    means "could not tell" — callers must record the event for manual
    reconciliation rather than guess which payment it belongs to."""
    direct = data.get("transaction_reference")
    if isinstance(direct, str) and direct:
        return direct
    transaction = data.get("transaction")
    if isinstance(transaction, dict):
        nested = transaction.get("reference")
        if isinstance(nested, str) and nested:
            return nested
    fallback = data.get("reference")
    return fallback if isinstance(fallback, str) else ""


def parse_positive_minor_amount(value: Any) -> Optional[int]:
    """A strictly positive integer amount in minor units, else None (missing,
    zero, negative, non-numeric, or a bool masquerading as a number)."""
    if isinstance(value, bool):
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def parse_refund_response(reference: str, data: dict[str, Any]) -> RefundResult:
    """Normalize the `data` object Paystack returns from POST /refund."""
    raw_id = data.get("id")
    return RefundResult(
        transaction_reference=reference,
        status=str(data.get("status") or "pending").lower(),
        amount_minor=parse_positive_minor_amount(data.get("amount")),
        currency=str(data["currency"]).upper() if data.get("currency") else None,
        refund_id=str(raw_id) if raw_id is not None else None,
    )
