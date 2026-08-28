"""M9 checkout: Razorpay test orders only after revalidation PASS."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, payment_provider
from app.layers.approval import approve, create_approval_request
from app.layers.basket import create_basket, live_subtotal, set_items
from app.layers.catalogue import get_variant_by_sku, set_on_hand_quantity
from app.layers.checkout import (
    CheckoutServiceError,
    checkout_idempotency_key,
    create_checkout_attempt,
    get_checkout_attempt,
    list_checkout_attempts,
    report_client_payment_result,
)
from app.layers.payments import (
    PaymentProviderError,
    RazorpayPaymentProvider,
    StubPaymentProvider,
    from_minor_units,
    to_minor_units,
)
from app.layers.session import create_session
from app.main import app
from app.models import CheckoutAttempt, Offer, Payment
from app.schemas.checkout import CheckoutPayload
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.vocabulary import (
    BudgetType,
    CheckoutAttemptStatus,
    PaymentStatus,
)

HERO = ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]
HERO_TOTAL = Decimal("2447.00")
HERO_PAISE = 244700


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500") -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal="complete_outfit",
        fit_preferences=["relaxed_waist"],
        occasion="farewell",
        usual_size="M",
    )


def _approve(
    db: Session,
    shopping,
    skus: list[str],
    *,
    offer_ref_id: str | None = None,
    action_ref_id: str = "ACT-CHK",
):
    basket = set_items(db, create_basket(db, shopping), skus)
    snapshot = {"offer_ref_id": offer_ref_id} if offer_ref_id else None
    request = create_approval_request(
        db, shopping, basket, action_ref_id=action_ref_id, snapshot=snapshot
    )
    approve(db, request.ref_id)
    return basket, request


def _checkout(db: Session, shopping, approval_ref: str, *, provider=None, basket=None, **claimed):
    return create_checkout_attempt(
        db,
        shopping,
        approval_ref,
        basket=basket,
        intent=_hard(),
        provider=provider or StubPaymentProvider(),
        **claimed,
    )


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


def test_checkout_status_vocabulary_is_closed() -> None:
    assert set(CheckoutAttemptStatus) == {
        CheckoutAttemptStatus.CREATED,
        CheckoutAttemptStatus.REVALIDATION_REQUIRED,
        CheckoutAttemptStatus.READY_FOR_PROVIDER,
        CheckoutAttemptStatus.ORDER_CREATED,
        CheckoutAttemptStatus.CHECKOUT_PRESENTED,
        CheckoutAttemptStatus.PAYMENT_REPORTED,
        CheckoutAttemptStatus.VERIFICATION_PENDING,
        CheckoutAttemptStatus.VERIFIED,
        CheckoutAttemptStatus.FAILED,
        CheckoutAttemptStatus.CANCELLED,
    }
    with pytest.raises(ValidationError):
        CheckoutPayload(
            checkout_attempt_ref="CHK-001",
            session_ref_id="SES-001",
            basket_ref_id="BASK-001",
            basket_version=1,
            approval_ref_id="APR-001",
            amount_minor=244700,
            currency="INR",
            provider="stub",
            status="PAID",  # type: ignore[arg-type]
        )


def test_inr_paise_conversion_is_integer() -> None:
    assert to_minor_units(HERO_TOTAL) == HERO_PAISE
    assert to_minor_units(Decimal("2447")) == HERO_PAISE
    assert from_minor_units(HERO_PAISE) == HERO_TOTAL
    assert isinstance(to_minor_units(HERO_TOTAL), int)
    with pytest.raises(PaymentProviderError):
        to_minor_units(Decimal("10.00"), currency="USD")


def test_stub_payment_provider_works_offline() -> None:
    stub = StubPaymentProvider()
    order = stub.create_order(
        amount_minor=HERO_PAISE,
        currency="INR",
        receipt="CHK-001",
        notes={"checkout_ref": "CHK-001"},
        idempotency_key="checkout:SES-001:BASK-001:v1:APR-001",
    )
    assert order.provider == "stub"
    assert order.provider_order_id == "order_stub_CHK-001"
    assert order.amount_minor == HERO_PAISE
    assert order.currency == "INR"
    with pytest.raises(PaymentProviderError) as exc:
        stub.verify_webhook(payload=b"{}", signature="x")
    assert exc.value.code == "not_implemented"


def test_razorpay_provider_requires_env_keys() -> None:
    with pytest.raises(PaymentProviderError) as exc:
        RazorpayPaymentProvider(key_id="", key_secret="")
    assert exc.value.code == "not_configured"


def test_hero_checkout_uses_authoritative_paise(db: Session) -> None:
    shopping = _session(db)
    basket, request = _approve(db, shopping, HERO)
    stub = StubPaymentProvider()
    payload = _checkout(db, shopping, request.ref_id, provider=stub)
    assert live_subtotal(db, basket) == HERO_TOTAL
    assert payload.amount_minor == HERO_PAISE
    assert payload.currency == "INR"
    assert payload.provider_order_id == f"order_stub_{payload.checkout_attempt_ref}"
    assert payload.checkout_attempt_ref.startswith("CHK-")
    assert payload.basket_ref_id == basket.ref_id
    assert payload.basket_version == 1
    assert payload.approval_ref_id == request.ref_id
    assert payload.revalidation_ref_id and payload.revalidation_ref_id.startswith("REVAL-")
    assert payload.status is CheckoutAttemptStatus.CHECKOUT_PRESENTED
    assert payload.status is not CheckoutAttemptStatus.VERIFIED
    row = get_checkout_attempt(db, payload.checkout_attempt_ref)
    assert row is not None
    assert row.provider_order_id == payload.provider_order_id
    assert row.amount_minor == HERO_PAISE
    assert row.approval_ref_id == request.ref_id
    assert row.revalidation_ref_id == payload.revalidation_ref_id
    assert row.idempotency_key == checkout_idempotency_key(
        session_ref_id=shopping.ref_id,
        basket_ref_id=basket.ref_id,
        basket_version=1,
        approval_ref_id=request.ref_id,
    )
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == row.id))
    assert payment is not None
    assert payment.ref_id.startswith("PAY-")
    assert payment.status == PaymentStatus.CREATED.value
    assert payment.verified_at is None
    assert payment.status != PaymentStatus.VERIFIED.value
    assert len(stub.created) == 1


def test_client_amount_is_rejected(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(
            db,
            shopping,
            request.ref_id,
            claimed_amount=Decimal("2000.00"),
        )
    assert exc.value.code == "client_amount_not_authoritative"
    with pytest.raises(CheckoutServiceError) as exc_minor:
        _checkout(
            db,
            shopping,
            request.ref_id,
            claimed_amount_minor=HERO_PAISE,
        )
    assert exc_minor.value.code == "client_amount_not_authoritative"
    assert list_checkout_attempts(db, shopping) == []


def test_pending_approval_blocks_checkout(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), HERO)
    pending = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, pending.ref_id)
    assert exc.value.code == "approval_not_granted"
    assert list_checkout_attempts(db, shopping) == []


def test_oos_blocks_checkout_and_creates_no_order(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, request.ref_id)
    assert exc.value.code == "revalidation_failed"
    assert "OUT_OF_STOCK" in exc.value.failure_reasons
    assert list_checkout_attempts(db, shopping) == []
    assert (
        db.scalar(
            select(func.count()).select_from(Payment).where(Payment.session_id == shopping.id)
        )
        == 0
    )


def test_price_change_blocks_checkout(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    variant = get_variant_by_sku(db, "SKU-007-M")
    variant.price_override = Decimal("899.00")
    db.flush()
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, request.ref_id)
    assert exc.value.code == "revalidation_failed"
    assert "PRICE_CHANGED" in exc.value.failure_reasons
    assert list_checkout_attempts(db, shopping) == []


def test_invalid_offer_blocks_checkout(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, ["SKU-001-M"], offer_ref_id="OFR-002")
    offer = db.scalar(select(Offer).where(Offer.ref_id == "OFR-002"))
    assert offer is not None
    offer.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    offer.is_active = False
    db.flush()
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, request.ref_id)
    assert exc.value.code == "revalidation_failed"
    assert list_checkout_attempts(db, shopping) == []


def test_stale_approval_blocks_checkout(db: Session) -> None:
    shopping = _session(db)
    v1, request = _approve(db, shopping, ["SKU-004-M"])
    v2 = set_items(db, v1, ["SKU-004-M", "SKU-007-M"])
    assert v2.version == 2
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, request.ref_id, basket=v2)
    assert exc.value.code == "revalidation_failed"
    assert any(
        code in exc.value.failure_reasons for code in ("STALE_APPROVAL", "BASKET_VERSION_MISMATCH")
    )
    assert list_checkout_attempts(db, shopping) == []


def test_hard_budget_violation_blocks_checkout(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    with pytest.raises(CheckoutServiceError) as exc:
        create_checkout_attempt(
            db,
            shopping,
            request.ref_id,
            intent=_hard("2000"),
            provider=StubPaymentProvider(),
        )
    assert exc.value.code == "revalidation_failed"
    assert list_checkout_attempts(db, shopping) == []


def test_duplicate_checkout_is_idempotent(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    stub = StubPaymentProvider()
    first = _checkout(db, shopping, request.ref_id, provider=stub)
    second = _checkout(db, shopping, request.ref_id, provider=stub)
    assert second.reused is True
    assert first.checkout_attempt_ref == second.checkout_attempt_ref
    assert first.provider_order_id == second.provider_order_id
    assert len(stub.created) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(CheckoutAttempt)
            .where(CheckoutAttempt.session_id == shopping.id)
        )
        == 1
    )


def test_provider_failure_is_failed_not_verified(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    failing = StubPaymentProvider(fail=True)
    with pytest.raises(CheckoutServiceError) as exc:
        _checkout(db, shopping, request.ref_id, provider=failing)
    assert exc.value.code == "provider_failed"
    rows = list_checkout_attempts(db, shopping)
    assert len(rows) == 1
    assert rows[0].status == CheckoutAttemptStatus.FAILED.value
    assert rows[0].provider_order_id is None
    payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == rows[0].id))
    assert payment is not None
    assert payment.status == PaymentStatus.FAILED.value
    assert payment.verified_at is None

    recovered = _checkout(db, shopping, request.ref_id, provider=StubPaymentProvider())
    assert recovered.checkout_attempt_ref == rows[0].ref_id
    assert recovered.status is CheckoutAttemptStatus.CHECKOUT_PRESENTED
    assert recovered.provider_order_id is not None
    retry_payment = db.scalar(select(Payment).where(Payment.checkout_attempt_id == rows[0].id))
    assert retry_payment.status == PaymentStatus.CREATED.value
    assert retry_payment.verified_at is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(CheckoutAttempt)
            .where(CheckoutAttempt.session_id == shopping.id)
        )
        == 1
    )


def test_client_success_cannot_mark_verified(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    payload = _checkout(db, shopping, request.ref_id)
    view = report_client_payment_result(
        db,
        payload.checkout_attempt_ref,
        razorpay_payment_id="pay_client_fake",
        razorpay_signature="not-a-real-signature",
        client_status="VERIFIED",
    )
    assert view.status is CheckoutAttemptStatus.VERIFICATION_PENDING
    assert view.status is not CheckoutAttemptStatus.VERIFIED
    assert view.payment_status is PaymentStatus.VERIFICATION_PENDING
    assert view.payment_verified_at is None
    payment = db.scalar(select(Payment).where(Payment.ref_id == view.payment_ref_id))
    assert payment.verified_at is None
    assert payment.status != PaymentStatus.VERIFIED.value


def test_health_and_checkout_http(api, db: Session) -> None:
    client, stub = api
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    shopping = _session(db)
    basket, request = _approve(db, shopping, HERO)
    rejected = client.post(
        "/api/v1/checkout",
        json={
            "session_ref_id": shopping.ref_id,
            "approval_ref_id": request.ref_id,
            "amount": "2000.00",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "client_amount_not_authoritative"

    created = client.post(
        "/api/v1/checkout",
        json={"session_ref_id": shopping.ref_id, "approval_ref_id": request.ref_id},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["amount_minor"] == HERO_PAISE
    assert body["basket_ref_id"] == basket.ref_id
    assert body["status"] == CheckoutAttemptStatus.CHECKOUT_PRESENTED.value
    assert body["provider_order_id"]
    assert "key_secret" not in body
    assert len(stub.created) == 1

    fetched = client.get(f"/api/v1/checkout/{body['checkout_attempt_ref']}")
    assert fetched.status_code == 200
    assert fetched.json()["provider_order_id"] == body["provider_order_id"]
    assert fetched.json()["payment_verified_at"] is None

    reported = client.post(
        f"/api/v1/checkout/{body['checkout_attempt_ref']}/client-result",
        json={"client_status": "success", "razorpay_payment_id": "pay_http"},
    )
    assert reported.status_code == 200
    assert reported.json()["status"] == CheckoutAttemptStatus.VERIFICATION_PENDING.value
    assert reported.json()["payment_status"] != PaymentStatus.VERIFIED.value


@pytest.mark.skipif(
    not (
        os.environ.get("MARGINMIND_LIVE_RAZORPAY") == "1"
        and os.environ.get("RAZORPAY_KEY_ID")
        and os.environ.get("RAZORPAY_KEY_SECRET")
    ),
    reason="Live Razorpay Test Mode is opt-in via MARGINMIND_LIVE_RAZORPAY=1",
)
def test_live_razorpay_test_mode_order(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    provider = RazorpayPaymentProvider(
        key_id=os.environ["RAZORPAY_KEY_ID"],
        key_secret=os.environ["RAZORPAY_KEY_SECRET"],
    )
    payload = _checkout(db, shopping, request.ref_id, provider=provider)
    assert payload.provider == "razorpay"
    assert payload.provider_order_id.startswith("order_")
    assert payload.amount_minor == HERO_PAISE
    assert payload.status is not CheckoutAttemptStatus.VERIFIED
