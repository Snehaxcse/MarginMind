"""Checkout attempts. Revalidation PASS is required before any provider order.

Client-supplied amounts are never authoritative. Client-reported payment
success is never VERIFIED (M10 verifies cryptographic proof / webhooks).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.layers.approval import get_approval
from app.layers.basket import get_basket, latest_basket_for_session, live_subtotal, version_label
from app.layers.checkout.errors import CheckoutServiceError
from app.layers.evidence import record_audit, record_evidence
from app.layers.payments import (
    PaymentProvider,
    PaymentProviderError,
    get_payment_provider,
    to_minor_units,
)
from app.layers.revalidation import revalidate_approved_basket
from app.models import CheckoutAttempt, Merchant, Payment, ShoppingSession
from app.schemas.checkout import CheckoutAttemptView, CheckoutPayload
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import (
    Actor,
    ApprovalStatus,
    CheckoutAttemptStatus,
    CheckoutState,
    EvidenceKind,
    PaymentStatus,
    RevalidationStatus,
)

CURRENCY = "INR"

_REUSABLE = {
    CheckoutAttemptStatus.ORDER_CREATED.value,
    CheckoutAttemptStatus.CHECKOUT_PRESENTED.value,
    CheckoutAttemptStatus.PAYMENT_REPORTED.value,
    CheckoutAttemptStatus.VERIFICATION_PENDING.value,
}

_RETRYABLE = {
    CheckoutAttemptStatus.FAILED.value,
    CheckoutAttemptStatus.CANCELLED.value,
    CheckoutAttemptStatus.CREATED.value,
    CheckoutAttemptStatus.READY_FOR_PROVIDER.value,
    CheckoutAttemptStatus.REVALIDATION_REQUIRED.value,
}

_CLIENT_RESULT_FROM = {
    CheckoutAttemptStatus.ORDER_CREATED.value,
    CheckoutAttemptStatus.CHECKOUT_PRESENTED.value,
    CheckoutAttemptStatus.PAYMENT_REPORTED.value,
    CheckoutAttemptStatus.VERIFICATION_PENDING.value,
}


def checkout_idempotency_key(
    *,
    session_ref_id: str,
    basket_ref_id: str,
    basket_version: int,
    approval_ref_id: str,
) -> str:
    """Stable key for one approved exact basket checkout.

    Repeated create-checkout for the same session + basket version + approval
    reuses the existing provider order when that attempt is still valid.
    Revalidation failures do not occupy this key, so a later restock can proceed.
    FAILED attempts with the same key are retried in place after a new PASS.
    """
    return f"checkout:{session_ref_id}:{basket_ref_id}:v{basket_version}:{approval_ref_id}"


def get_checkout_attempt(db: Session, ref_id: str) -> CheckoutAttempt | None:
    return db.scalar(
        select(CheckoutAttempt)
        .options(selectinload(CheckoutAttempt.payments), selectinload(CheckoutAttempt.session))
        .where(CheckoutAttempt.ref_id == ref_id)
    )


def list_checkout_attempts(db: Session, shopping: ShoppingSession) -> list[CheckoutAttempt]:
    return list(
        db.scalars(
            select(CheckoutAttempt)
            .options(selectinload(CheckoutAttempt.payments))
            .where(CheckoutAttempt.session_id == shopping.id)
            .order_by(CheckoutAttempt.created_at.asc())
        ).all()
    )


def checkout_view(db: Session, row: CheckoutAttempt) -> CheckoutAttemptView:
    payment = _payment_for(db, row)
    session_ref = row.session.ref_id if row.session is not None else ""
    return CheckoutAttemptView(
        ref_id=row.ref_id,
        session_ref_id=session_ref,
        basket_ref_id=row.basket_ref_id,
        basket_version=row.basket_version,
        approval_ref_id=row.approval_ref_id,
        revalidation_ref_id=row.revalidation_ref_id,
        amount_minor=row.amount_minor,
        currency=row.currency,
        provider=row.provider,
        provider_order_id=row.provider_order_id,
        status=CheckoutAttemptStatus(row.status),
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        payment_ref_id=None if payment is None else payment.ref_id,
        payment_status=None if payment is None else PaymentStatus(payment.status),
        payment_verified_at=None if payment is None else payment.verified_at,
    )


def create_checkout_attempt(
    db: Session,
    shopping: ShoppingSession,
    approval_ref_id: str,
    *,
    basket=None,
    intent: ShopperIntent | None = None,
    provider: PaymentProvider | None = None,
    claimed_amount: Decimal | None = None,
    claimed_amount_minor: int | None = None,
    claimed_price: Decimal | None = None,
    claimed_discounted_total: Decimal | None = None,
    display_name: str | None = None,
) -> CheckoutPayload:
    """Approved exact basket → M8 PASS → idempotent CheckoutAttempt → provider order."""
    _reject_client_amount(
        claimed_amount=claimed_amount,
        claimed_amount_minor=claimed_amount_minor,
        claimed_price=claimed_price,
        claimed_discounted_total=claimed_discounted_total,
    )
    pay = provider or get_payment_provider()
    approval = get_approval(db, approval_ref_id)
    if approval is None:
        raise CheckoutServiceError("unknown_approval", f"Approval {approval_ref_id} was not found.")
    if approval.session_id != shopping.id:
        raise CheckoutServiceError(
            "session_mismatch",
            "Approval does not belong to this session.",
        )
    if approval.status != ApprovalStatus.GRANTED.value:
        raise CheckoutServiceError(
            "approval_not_granted",
            f"Approval {approval.ref_id} is {approval.status}, not granted.",
        )

    claimed = basket
    if claimed is None:
        claimed = latest_basket_for_session(db, shopping)
    approved_basket = get_basket(db, approval.basket.ref_id, version=approval.basket_version)
    if approved_basket is None:
        raise CheckoutServiceError(
            "unknown_basket",
            "The approved basket version is missing.",
        )

    revalidation = revalidate_approved_basket(
        db,
        shopping,
        approval.ref_id,
        basket=claimed,
        intent=intent,
    )
    if revalidation.status is not RevalidationStatus.PASS:
        raise CheckoutServiceError(
            "revalidation_failed",
            "Final revalidation did not PASS; no payment order was created.",
            failure_reasons=list(revalidation.failure_reasons),
            revalidation_ref_id=revalidation.ref_id,
        )

    amount = live_subtotal(db, approved_basket)
    amount_minor = to_minor_units(amount, currency=CURRENCY)
    key = checkout_idempotency_key(
        session_ref_id=shopping.ref_id,
        basket_ref_id=approved_basket.ref_id,
        basket_version=approved_basket.version,
        approval_ref_id=approval.ref_id,
    )
    merchant_name = display_name or _merchant_name(db, shopping)
    existing = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.idempotency_key == key))

    if existing is not None and existing.status in _REUSABLE:
        if existing.amount_minor != amount_minor:
            existing.status = CheckoutAttemptStatus.FAILED.value
            existing.updated_at = datetime.now(timezone.utc)
            db.flush()
            raise CheckoutServiceError(
                "amount_mismatch",
                "Existing checkout amount no longer matches catalogue truth.",
                checkout_attempt_ref=existing.ref_id,
            )
        existing.revalidation_ref_id = revalidation.ref_id
        existing.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _payload(
            existing,
            pay,
            session_ref_id=shopping.ref_id,
            merchant_name=merchant_name,
            reused=True,
        )

    attempt = existing
    if attempt is None:
        attempt = CheckoutAttempt(
            ref_id=next_numeric_ref_id(db, CheckoutAttempt, RefPrefix.CHECKOUT),
            session_id=shopping.id,
            basket_id=approved_basket.id,
            basket_ref_id=approved_basket.ref_id,
            basket_version=approved_basket.version,
            approval_ref_id=approval.ref_id,
            revalidation_ref_id=revalidation.ref_id,
            amount_minor=amount_minor,
            currency=CURRENCY,
            provider=pay.name,
            provider_order_id=None,
            status=CheckoutAttemptStatus.CREATED.value,
            idempotency_key=key,
        )
        db.add(attempt)
        db.flush()
    elif attempt.status in _RETRYABLE:
        attempt.revalidation_ref_id = revalidation.ref_id
        attempt.amount_minor = amount_minor
        attempt.currency = CURRENCY
        attempt.provider = pay.name
        attempt.status = CheckoutAttemptStatus.CREATED.value
        attempt.updated_at = datetime.now(timezone.utc)
        db.flush()
    else:
        raise CheckoutServiceError(
            "checkout_not_retryable",
            f"Checkout {attempt.ref_id} is {attempt.status} and cannot create a new order.",
            checkout_attempt_ref=attempt.ref_id,
        )

    attempt.status = CheckoutAttemptStatus.READY_FOR_PROVIDER.value
    db.flush()
    try:
        order = pay.create_order(
            amount_minor=amount_minor,
            currency=CURRENCY,
            receipt=attempt.ref_id,
            notes={
                "checkout_ref": attempt.ref_id,
                "session_ref": shopping.ref_id,
                "basket_ref": version_label(approved_basket),
                "approval_ref": approval.ref_id,
            },
            idempotency_key=key,
        )
    except PaymentProviderError as exc:
        attempt.status = CheckoutAttemptStatus.FAILED.value
        attempt.updated_at = datetime.now(timezone.utc)
        payment = _ensure_payment(db, shopping, attempt)
        payment.status = PaymentStatus.FAILED.value
        payment.verified_at = None
        db.flush()
        _record(
            db,
            shopping,
            attempt,
            decision="FAILED",
            summary=f"Provider order failed for {attempt.ref_id}",
            extra={"provider_code": exc.code},
        )
        raise CheckoutServiceError(
            "provider_failed",
            "Payment provider did not create an order.",
            checkout_attempt_ref=attempt.ref_id,
        ) from exc

    if order.amount_minor != amount_minor or order.currency != CURRENCY:
        attempt.status = CheckoutAttemptStatus.FAILED.value
        attempt.updated_at = datetime.now(timezone.utc)
        payment = _ensure_payment(db, shopping, attempt)
        payment.status = PaymentStatus.FAILED.value
        payment.verified_at = None
        db.flush()
        raise CheckoutServiceError(
            "provider_failed",
            "Provider order amount did not match catalogue truth.",
            checkout_attempt_ref=attempt.ref_id,
        )

    attempt.provider = order.provider
    attempt.provider_order_id = order.provider_order_id
    attempt.amount_minor = order.amount_minor
    attempt.currency = order.currency
    attempt.status = CheckoutAttemptStatus.ORDER_CREATED.value
    attempt.updated_at = datetime.now(timezone.utc)
    if approved_basket.status != CheckoutState.VERIFIED.value:
        approved_basket.status = CheckoutState.ORDER_CREATED.value
    payment = _ensure_payment(db, shopping, attempt)
    payment.provider = order.provider
    payment.provider_order_id = order.provider_order_id
    payment.amount_minor = order.amount_minor
    payment.currency = order.currency
    payment.status = PaymentStatus.CREATED.value
    payment.verified_at = None
    attempt.status = CheckoutAttemptStatus.CHECKOUT_PRESENTED.value
    db.flush()
    _record(
        db,
        shopping,
        attempt,
        decision="CHECKOUT_PRESENTED",
        summary=f"Provider order created for {attempt.ref_id}",
        extra={"provider_order_id": order.provider_order_id, "amount_minor": amount_minor},
    )
    return _payload(
        attempt,
        pay,
        session_ref_id=shopping.ref_id,
        merchant_name=merchant_name,
        reused=False,
        key_id=order.key_id,
    )


def report_client_payment_result(
    db: Session,
    checkout_ref_id: str,
    *,
    provider_payment_id: str | None = None,
    razorpay_payment_id: str | None = None,
    razorpay_order_id: str | None = None,
    razorpay_signature: str | None = None,
    client_status: str | None = None,
) -> CheckoutAttemptView:
    """Record a browser callback as reported, never as VERIFIED."""
    attempt = get_checkout_attempt(db, checkout_ref_id)
    if attempt is None:
        raise CheckoutServiceError("unknown_checkout", f"Checkout {checkout_ref_id} was not found.")
    if attempt.status == CheckoutAttemptStatus.VERIFIED.value:
        raise CheckoutServiceError(
            "checkout_not_reportable",
            f"Checkout {attempt.ref_id} is already VERIFIED; client results cannot change it.",
            checkout_attempt_ref=attempt.ref_id,
        )
    if attempt.status not in _CLIENT_RESULT_FROM:
        raise CheckoutServiceError(
            "checkout_not_reportable",
            f"Checkout {attempt.ref_id} is {attempt.status}; client results are not accepted.",
            checkout_attempt_ref=attempt.ref_id,
        )
    payment_id = provider_payment_id or razorpay_payment_id
    payload: dict[str, Any] = {
        "provider_payment_id": payment_id,
        "razorpay_order_id": razorpay_order_id,
        "client_status": client_status,
        "signature_present": bool(razorpay_signature),
    }
    now = datetime.now(timezone.utc)
    if payment_id:
        next_checkout = CheckoutAttemptStatus.VERIFICATION_PENDING.value
        next_payment = PaymentStatus.VERIFICATION_PENDING.value
    else:
        next_checkout = CheckoutAttemptStatus.PAYMENT_REPORTED.value
        next_payment = PaymentStatus.REPORTED.value
    attempt.status = next_checkout
    attempt.updated_at = now
    payment = _ensure_payment(db, attempt.session, attempt)
    payment.provider_payment_id = payment_id
    payment.reported_at = now
    payment.verified_at = None
    payment.status = next_payment
    payment.client_payload = payload
    db.flush()
    shopping = attempt.session
    _record(
        db,
        shopping,
        attempt,
        decision=next_checkout,
        summary=f"Client payment result recorded for {attempt.ref_id}",
        extra={"client_status": client_status, "treated_as": next_checkout},
    )
    return checkout_view(db, attempt)


def _reject_client_amount(
    *,
    claimed_amount: Decimal | None,
    claimed_amount_minor: int | None,
    claimed_price: Decimal | None,
    claimed_discounted_total: Decimal | None,
) -> None:
    if any(
        value is not None
        for value in (claimed_amount, claimed_amount_minor, claimed_price, claimed_discounted_total)
    ):
        raise CheckoutServiceError(
            "client_amount_not_authoritative",
            "Caller-supplied amount/price is not accepted; the server computes paise from catalogue truth.",
        )


def _merchant_name(db: Session, shopping: ShoppingSession) -> str:
    settings = get_settings()
    merchant = db.get(Merchant, shopping.merchant_id)
    if merchant is not None and merchant.name:
        return merchant.name
    return settings.checkout_display_name


def _payment_for(db: Session, attempt: CheckoutAttempt) -> Payment | None:
    if attempt.payments:
        return attempt.payments[0]
    return db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))


def _ensure_payment(db: Session, shopping: ShoppingSession, attempt: CheckoutAttempt) -> Payment:
    existing = _payment_for(db, attempt)
    if existing is not None:
        return existing
    row = Payment(
        ref_id=next_numeric_ref_id(db, Payment, RefPrefix.PAYMENT),
        session_id=shopping.id,
        checkout_attempt_id=attempt.id,
        provider=attempt.provider,
        provider_order_id=attempt.provider_order_id,
        provider_payment_id=None,
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        status=PaymentStatus.CREATED.value,
        reported_at=None,
        verified_at=None,
        client_payload={},
    )
    db.add(row)
    db.flush()
    return row


def _payload(
    attempt: CheckoutAttempt,
    provider: PaymentProvider,
    *,
    session_ref_id: str,
    merchant_name: str,
    reused: bool,
    key_id: str | None = None,
) -> CheckoutPayload:
    public_key = key_id
    if public_key is None:
        public_key = getattr(provider, "key_id", None)
    return CheckoutPayload(
        checkout_attempt_ref=attempt.ref_id,
        session_ref_id=session_ref_id,
        basket_ref_id=attempt.basket_ref_id,
        basket_version=attempt.basket_version,
        approval_ref_id=attempt.approval_ref_id,
        revalidation_ref_id=attempt.revalidation_ref_id,
        provider=attempt.provider,
        provider_order_id=attempt.provider_order_id,
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        key_id=public_key,
        merchant_name=merchant_name,
        status=CheckoutAttemptStatus(attempt.status),
        reused=reused,
    )


def _record(
    db: Session,
    shopping: ShoppingSession,
    attempt: CheckoutAttempt,
    *,
    decision: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> None:
    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.CHECKOUT.value,
        summary=summary,
        payload={
            "checkout_ref": attempt.ref_id,
            "approval_ref": attempt.approval_ref_id,
            "revalidation_ref": attempt.revalidation_ref_id,
            "amount_minor": attempt.amount_minor,
            "status": attempt.status,
            **(extra or {}),
        },
    )
    record_audit(
        db,
        session=shopping,
        actor=Actor.SYSTEM.value,
        event_type="checkout",
        decision=decision,
        evidence_ref_ids=[evidence.ref_id],
        payload={
            "checkout_ref": attempt.ref_id,
            "provider_order_id": attempt.provider_order_id,
            "status": attempt.status,
        },
    )
