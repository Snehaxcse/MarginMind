"""M10: raw-body HMAC webhook verification. Client success is not VERIFIED."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, payment_provider
from app.layers.approval import approve, create_approval_request
from app.layers.basket import create_basket, set_items
from app.layers.checkout import (
    confirm_payment_from_provider,
    create_checkout_attempt,
    get_checkout_attempt,
    list_webhook_events,
    process_webhook,
    report_client_payment_result,
)
from app.layers.payments import (
    PaymentSnapshot,
    PaymentProviderError,
    RazorpayPaymentProvider,
    StubPaymentProvider,
    encode_razorpay_event,
    hmac_sha256_hex,
    signatures_match,
)
from app.layers.session import create_session
from app.main import app
from app.models import AuditEvent, Payment, WebhookEvent
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.vocabulary import (
    BudgetType,
    CheckoutAttemptStatus,
    PaymentStatus,
    WebhookProcessingStatus,
)

HERO = ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]
HERO_PAISE = 244700


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard() -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal("2500"), type=BudgetType.HARD),
        goal="complete_outfit",
        fit_preferences=["relaxed_waist"],
        occasion="farewell",
        usual_size="M",
    )


def _approve(db: Session, shopping, skus: list[str] = HERO):
    basket = set_items(db, create_basket(db, shopping), skus)
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    approve(db, request.ref_id)
    return basket, request


def _hero_checkout(db: Session, *, stub: StubPaymentProvider | None = None):
    shopping = _session(db)
    _basket, request = _approve(db, shopping)
    provider = stub or StubPaymentProvider()
    payload = create_checkout_attempt(
        db, shopping, request.ref_id, intent=_hard(), provider=provider
    )
    return shopping, payload, provider


def _signed(stub: StubPaymentProvider, payload, *, event, payment_id, amount=HERO_PAISE, currency="INR", status="captured"):
    body = encode_razorpay_event(
        event=event,
        order_id=payload.provider_order_id,
        payment_id=payment_id,
        amount_minor=amount,
        currency=currency,
        status=status,
    )
    return body, stub.sign_webhook(body)


@pytest.fixture
def api(db: Session):
    stub = StubPaymentProvider()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[payment_provider] = lambda: stub
    with TestClient(app) as client:
        yield client, stub
    app.dependency_overrides.clear()


def test_webhook_status_vocabulary_is_closed() -> None:
    assert set(WebhookProcessingStatus) == {
        WebhookProcessingStatus.RECEIVED,
        WebhookProcessingStatus.VERIFIED_SIGNATURE,
        WebhookProcessingStatus.PROCESSED,
        WebhookProcessingStatus.DUPLICATE,
        WebhookProcessingStatus.IGNORED,
        WebhookProcessingStatus.FAILED,
    }
    assert PaymentStatus.AUTHORIZED in PaymentStatus


def test_hmac_verifies_raw_body_not_reparsed_json() -> None:
    stub = StubPaymentProvider()
    original = encode_razorpay_event(
        event="payment.captured",
        order_id="order_stub_CHK-001",
        payment_id="pay_1",
        amount_minor=HERO_PAISE,
    )
    signature = stub.sign_webhook(original)
    assert signatures_match(secret=stub.webhook_secret, body=original, signature=signature)
    parsed = json.loads(original.decode("utf-8"))
    reparsed = json.dumps(parsed, indent=2).encode("utf-8")
    assert reparsed != original
    assert not signatures_match(secret=stub.webhook_secret, body=reparsed, signature=signature)
    key_sig = hmac_sha256_hex(secret=stub.key_secret, body=original)
    assert key_sig != signature
    with pytest.raises(PaymentProviderError) as exc:
        stub.verify_webhook(payload=original, signature=key_sig)
    assert exc.value.code == "invalid_signature"


def test_razorpay_webhook_secret_is_not_key_secret() -> None:
    provider = RazorpayPaymentProvider(
        key_id="rzp_test_x",
        key_secret="key_secret",
        webhook_secret="webhook_secret",
    )
    body = encode_razorpay_event(
        event="payment.captured",
        order_id="order_1",
        payment_id="pay_1",
        amount_minor=HERO_PAISE,
    )
    wrong = hmac_sha256_hex(secret="key_secret", body=body)
    with pytest.raises(PaymentProviderError) as exc:
        provider.verify_webhook(payload=body, signature=wrong)
    assert exc.value.code == "invalid_signature"
    good = hmac_sha256_hex(secret="webhook_secret", body=body)
    envelope = provider.verify_webhook(payload=body, signature=good)
    assert envelope.event_type == "payment.captured"


def test_hero_client_success_then_captured_verifies(db: Session) -> None:
    shopping, payload, stub = _hero_checkout(db)
    reported = report_client_payment_result(
        db,
        payload.checkout_attempt_ref,
        razorpay_payment_id="pay_client",
        client_status="VERIFIED",
    )
    assert reported.status is CheckoutAttemptStatus.VERIFICATION_PENDING
    assert reported.payment_status is not PaymentStatus.VERIFIED
    assert reported.payment_verified_at is None

    body, signature = _signed(stub, payload, event="payment.captured", payment_id="pay_hero")
    result = process_webhook(
        db, stub, body=body, signature=signature, event_id="evt_captured_1"
    )
    assert result.http_status == 200
    assert result.processing_status is WebhookProcessingStatus.PROCESSED
    assert result.payment_verified is True
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert attempt.status == CheckoutAttemptStatus.VERIFIED.value
    assert payment.status == PaymentStatus.VERIFIED.value
    assert payment.verified_at is not None
    assert payment.provider_payment_id == "pay_hero"
    assert payment.amount_minor == HERO_PAISE
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.session_id == shopping.id,
                AuditEvent.event_type == "payment_verified",
            )
        ).all()
    )
    assert len(audits) == 1


def test_missing_and_invalid_signature_do_not_verify(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, good = _signed(stub, payload, event="payment.captured", payment_id="pay_x")
    missing = process_webhook(db, stub, body=body, signature=None, event_id="evt_miss")
    assert missing.http_status == 400
    assert missing.failure_reason == "MISSING_SIGNATURE"
    invalid = process_webhook(db, stub, body=body, signature="00" * 32, event_id="evt_bad")
    assert invalid.http_status == 400
    assert invalid.failure_reason == "INVALID_SIGNATURE"
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value
    assert payment.status != PaymentStatus.VERIFIED.value
    assert payment.verified_at is None


def test_duplicate_provider_event_is_idempotent(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(stub, payload, event="payment.captured", payment_id="pay_dup")
    first = process_webhook(db, stub, body=body, signature=signature, event_id="evt_dup")
    second = process_webhook(db, stub, body=body, signature=signature, event_id="evt_dup")
    assert first.processing_status is WebhookProcessingStatus.PROCESSED
    assert second.processing_status is WebhookProcessingStatus.DUPLICATE
    assert second.http_status == 200
    assert first.webhook_ref == second.webhook_ref
    assert (
        db.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(WebhookEvent.provider_event_id == "evt_dup")
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "payment_verified")
        )
        >= 1
    )
    verified_audits = list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_type == "payment_verified")).all()
    )
    # one commercial verification even if earlier tests left rows; scope to this checkout
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    mine = [
        row
        for row in verified_audits
        if row.session_id == attempt.session_id
    ]
    assert len(mine) == 1


def test_conflicting_duplicate_event_id_fails_safely(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(stub, payload, event="payment.captured", payment_id="pay_a")
    process_webhook(db, stub, body=body, signature=signature, event_id="evt_conflict")
    other, other_sig = _signed(stub, payload, event="payment.captured", payment_id="pay_b")
    conflict = process_webhook(
        db, stub, body=other, signature=other_sig, event_id="evt_conflict"
    )
    assert conflict.http_status == 409
    assert conflict.failure_reason == "EVENT_ID_BODY_CONFLICT"
    payment = db.scalar(
        select(Payment).where(
            Payment.checkout_attempt_id == get_checkout_attempt(db, payload.checkout_attempt_ref).id
        )
    )
    assert payment.provider_payment_id == "pay_a"
    assert payment.status == PaymentStatus.VERIFIED.value


def test_out_of_order_authorized_does_not_downgrade(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    captured, cap_sig = _signed(
        stub, payload, event="payment.captured", payment_id="pay_oo", status="captured"
    )
    process_webhook(db, stub, body=captured, signature=cap_sig, event_id="evt_cap_first")
    authorized, auth_sig = _signed(
        stub, payload, event="payment.authorized", payment_id="pay_oo", status="authorized"
    )
    later = process_webhook(
        db, stub, body=authorized, signature=auth_sig, event_id="evt_auth_later"
    )
    assert later.processing_status is WebhookProcessingStatus.PROCESSED
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert attempt.status == CheckoutAttemptStatus.VERIFIED.value
    assert payment.status == PaymentStatus.VERIFIED.value


def test_authorized_is_not_verified(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(
        stub, payload, event="payment.authorized", payment_id="pay_auth", status="authorized"
    )
    result = process_webhook(db, stub, body=body, signature=signature, event_id="evt_auth")
    assert result.payment_verified is False
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert payment.status == PaymentStatus.AUTHORIZED.value
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value
    assert payment.verified_at is None


def test_captured_then_order_paid_applies_once(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    captured, cap_sig = _signed(stub, payload, event="payment.captured", payment_id="pay_once")
    paid, paid_sig = _signed(
        stub, payload, event="order.paid", payment_id="pay_once", status="captured"
    )
    first = process_webhook(db, stub, body=captured, signature=cap_sig, event_id="evt_cap")
    second = process_webhook(db, stub, body=paid, signature=paid_sig, event_id="evt_paid")
    assert first.payment_verified is True
    assert second.processing_status is WebhookProcessingStatus.PROCESSED
    rows = list_webhook_events(db, checkout_ref_id=payload.checkout_attempt_ref)
    assert len(rows) == 2
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert payment.status == PaymentStatus.VERIFIED.value
    mine = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.session_id == attempt.session_id,
                AuditEvent.event_type == "payment_verified",
            )
        ).all()
    )
    assert len(mine) == 1


def test_order_paid_alone_verifies(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(
        stub, payload, event="order.paid", payment_id="pay_order_paid", status="captured"
    )
    result = process_webhook(db, stub, body=body, signature=signature, event_id="evt_order_paid")
    assert result.payment_verified is True
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert attempt.status == CheckoutAttemptStatus.VERIFIED.value


def test_amount_mismatch_does_not_verify(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(
        stub, payload, event="payment.captured", payment_id="pay_amt", amount=200000
    )
    result = process_webhook(db, stub, body=body, signature=signature, event_id="evt_amt")
    assert result.http_status == 200
    assert result.processing_status is WebhookProcessingStatus.FAILED
    assert result.failure_reason == "PAYMENT_AMOUNT_MISMATCH"
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value
    assert payment.verified_at is None


def test_currency_mismatch_does_not_verify(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body, signature = _signed(
        stub,
        payload,
        event="payment.captured",
        payment_id="pay_ccy",
        currency="USD",
    )
    result = process_webhook(db, stub, body=body, signature=signature, event_id="evt_ccy")
    assert result.failure_reason == "CURRENCY_MISMATCH"
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value


def test_unknown_order_is_not_attached(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body = encode_razorpay_event(
        event="payment.captured",
        order_id="order_someone_else",
        payment_id="pay_other",
        amount_minor=HERO_PAISE,
    )
    result = process_webhook(
        db, stub, body=body, signature=stub.sign_webhook(body), event_id="evt_unknown"
    )
    assert result.processing_status is WebhookProcessingStatus.IGNORED
    assert result.failure_reason == "UNKNOWN_ORDER"
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value


def test_failed_payment_and_no_downgrade_after_verified(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    failed, fail_sig = _signed(
        stub, payload, event="payment.failed", payment_id="pay_fail", status="failed"
    )
    first = process_webhook(db, stub, body=failed, signature=fail_sig, event_id="evt_fail")
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert payment.status == PaymentStatus.FAILED.value
    assert attempt.status == CheckoutAttemptStatus.FAILED.value
    assert first.payment_verified is False

    captured, cap_sig = _signed(
        stub, payload, event="payment.captured", payment_id="pay_fail", status="captured"
    )
    recovered = process_webhook(
        db, stub, body=captured, signature=cap_sig, event_id="evt_late_cap"
    )
    assert recovered.payment_verified is True
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert payment.status == PaymentStatus.VERIFIED.value

    failed_again, fail_sig2 = _signed(
        stub, payload, event="payment.failed", payment_id="pay_fail", status="failed"
    )
    later_fail = process_webhook(
        db, stub, body=failed_again, signature=fail_sig2, event_id="evt_fail_after"
    )
    assert later_fail.processing_status is WebhookProcessingStatus.PROCESSED
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))
    assert attempt.status == CheckoutAttemptStatus.VERIFIED.value
    assert payment.status == PaymentStatus.VERIFIED.value
    assert payment.verified_at is not None


def test_unsupported_event_is_ignored(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    body = encode_razorpay_event(
        event="refund.created",
        order_id=payload.provider_order_id,
        payment_id="pay_ref",
        amount_minor=HERO_PAISE,
    )
    result = process_webhook(
        db, stub, body=body, signature=stub.sign_webhook(body), event_id="evt_refund"
    )
    assert result.http_status == 200
    assert result.processing_status is WebhookProcessingStatus.IGNORED
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert attempt.status != CheckoutAttemptStatus.VERIFIED.value


def test_client_result_cannot_mark_verified_after_m10(db: Session) -> None:
    _shopping, payload, _stub = _hero_checkout(db)
    view = report_client_payment_result(
        db,
        payload.checkout_attempt_ref,
        client_status="success",
        razorpay_payment_id="pay_browser",
    )
    assert view.status is CheckoutAttemptStatus.VERIFICATION_PENDING
    assert view.payment_status is PaymentStatus.VERIFICATION_PENDING
    assert view.payment_verified_at is None


def test_http_webhook_and_client_result(api, db: Session) -> None:
    client, stub = api
    shopping = _session(db)
    _basket, request = _approve(db, shopping)
    created = client.post(
        "/api/v1/checkout",
        json={"session_ref_id": shopping.ref_id, "approval_ref_id": request.ref_id},
    )
    assert created.status_code == 200
    checkout = created.json()
    reported = client.post(
        f"/api/v1/checkout/{checkout['checkout_attempt_ref']}/client-result",
        json={"client_status": "VERIFIED", "razorpay_payment_id": "pay_http"},
    )
    assert reported.status_code == 200
    assert reported.json()["status"] != CheckoutAttemptStatus.VERIFIED.value

    body = encode_razorpay_event(
        event="payment.captured",
        order_id=checkout["provider_order_id"],
        payment_id="pay_http_cap",
        amount_minor=HERO_PAISE,
    )
    response = client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": stub.sign_webhook(body),
            "X-Razorpay-Event-Id": "evt_http_1",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == WebhookProcessingStatus.PROCESSED.value
    assert response.json()["payment_verified"] is True
    fetched = client.get(f"/api/v1/checkout/{checkout['checkout_attempt_ref']}")
    assert fetched.json()["status"] == CheckoutAttemptStatus.VERIFIED.value
    assert fetched.json()["payment_status"] == PaymentStatus.VERIFIED.value
    assert fetched.json()["payment_verified_at"] is not None

    replay = client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": stub.sign_webhook(body),
            "X-Razorpay-Event-Id": "evt_http_1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True


def test_fetch_payment_recovery_offline(db: Session) -> None:
    _shopping, payload, stub = _hero_checkout(db)
    stub.payments["pay_fetch"] = PaymentSnapshot(
        provider="stub",
        provider_payment_id="pay_fetch",
        provider_order_id=payload.provider_order_id,
        amount_minor=HERO_PAISE,
        currency="INR",
        status="captured",
    )
    result = confirm_payment_from_provider(
        db, stub, payload.checkout_attempt_ref, "pay_fetch"
    )
    assert result.payment_verified is True
    attempt = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert attempt.status == CheckoutAttemptStatus.VERIFIED.value
