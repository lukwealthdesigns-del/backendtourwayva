from datetime import timezone

from app.providers.payments.paystack_parsing import (
    compute_signature,
    extract_transaction_reference,
    parse_positive_minor_amount,
    parse_refund_response,
    parse_transaction,
    signature_matches,
)

SECRET = "sk_test_secret"
BODY = b'{"event":"charge.success","data":{"reference":"TW-1"}}'


def test_signature_roundtrip_and_tamper_detection():
    sig = compute_signature(SECRET, BODY)
    assert len(sig) == 128                                  # HMAC-SHA512 hex digest
    assert signature_matches(SECRET, BODY, sig)
    assert signature_matches(SECRET, BODY, sig.upper())     # header case is not significant
    assert not signature_matches(SECRET, BODY + b" ", sig)  # any change to the body breaks it
    assert not signature_matches("other-secret", BODY, sig)


def test_missing_signature_or_secret_never_matches():
    sig = compute_signature(SECRET, BODY)
    assert not signature_matches(SECRET, BODY, None)
    assert not signature_matches(SECRET, BODY, "")
    assert not signature_matches("", BODY, sig)


def test_parse_transaction_normalizes_fields():
    tx = parse_transaction(
        {
            "reference": "TW-1", "status": "Success", "amount": 500000, "currency": "ngn",
            "paid_at": "2026-09-19T21:10:19.000Z", "gateway_response": "Successful",
            "customer": {"email": "a@b.co", "customer_code": "CUS_1"},
            "plan": {"plan_code": "PLN_1"}, "metadata": '{"user_id": "u1"}',
        }
    )
    assert (tx.reference, tx.status, tx.amount_minor, tx.currency) == ("TW-1", "success", 500000, "NGN")
    assert tx.customer_code == "CUS_1" and tx.plan_code == "PLN_1"
    assert tx.metadata == {"user_id": "u1"}
    assert tx.paid_at.tzinfo is not None and tx.paid_at.astimezone(timezone.utc).year == 2026


def test_parse_transaction_is_defensive_about_odd_payloads():
    tx = parse_transaction({"reference": "x", "status": "failed", "amount": "not-a-number", "plan": {}, "metadata": None})
    assert tx.amount_minor == 0 and tx.plan_code is None and tx.metadata == {} and tx.paid_at is None
    assert parse_transaction({"reference": "x", "plan": "PLN_str"}).plan_code == "PLN_str"


def test_transaction_reference_is_found_wherever_the_event_family_puts_it():
    assert extract_transaction_reference({"transaction_reference": "TW-1"}) == "TW-1"      # refund.*
    assert extract_transaction_reference({"transaction": {"reference": "TW-2"}}) == "TW-2"  # charge.dispute.*
    assert extract_transaction_reference({"reference": "TW-3"}) == "TW-3"                   # last resort
    assert extract_transaction_reference({"transaction_reference": "TW-1", "reference": "TW-9"}) == "TW-1"


def test_a_missing_or_malformed_transaction_reference_is_empty_never_guessed():
    assert extract_transaction_reference({}) == ""
    assert extract_transaction_reference({"transaction": "not-a-dict"}) == ""
    assert extract_transaction_reference({"transaction": {"reference": None}}) == ""
    assert extract_transaction_reference({"transaction_reference": 123}) == ""


def test_refund_amounts_must_be_strictly_positive_integers():
    assert parse_positive_minor_amount(5000) == 5000
    assert parse_positive_minor_amount("5000") == 5000
    for bad in (0, -1, None, "", "abc", True, False, 0.0):
        assert parse_positive_minor_amount(bad) is None, bad


def test_refund_response_is_normalized_with_safe_defaults():
    parsed = parse_refund_response("TW-1", {"status": "PENDING", "amount": 250000, "currency": "ngn", "id": 77})
    assert (parsed.transaction_reference, parsed.status, parsed.amount_minor) == ("TW-1", "pending", 250000)
    assert parsed.currency == "NGN" and parsed.refund_id == "77"

    bare = parse_refund_response("TW-2", {})
    assert bare.status == "pending" and bare.amount_minor is None and bare.currency is None and bare.refund_id is None
