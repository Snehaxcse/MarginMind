"""Idempotent webhook application. Signature-valid ≠ payment VERIFIED."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.layers.evidence import record_audit, record_evidence
from app.layers.payments import PaymentProvider, PaymentProviderError, PaymentSnapshot
from app.layers.payments.signature import sha256_hex
from app.layers.payments.webhook import (
    SUPPORTED_WEBHOOK_EVENTS,
    VerifiedWebhookEnvelope,
)
from app.models import CheckoutAttempt, Payment, ShoppingSession, WebhookEvent
from app.schemas.vocabulary import (
    Actor,
    CheckoutAttemptStatus,
    CheckoutState,
    EvidenceKind,
    PaymentStatus,
    WebhookProcessingStatus,
)

EXPECTED_CURRENCY = "INR"

_NO_DOWNGRADE = {
    CheckoutAttemptStatus.VERIFIED.value,
    PaymentStatus.VERIFIED.value,
}


@dataclass
class WebhookProcessResult:
    http_status: int
    processing_status: WebhookProcessingStatus
    webhook_ref: str | None = None
    failure_reason: str | None = None
    duplicate: bool = False
    payment_verified: bool = False


def process_webhook(
    db: Session,
    provider: PaymentProvider,
    *,
    body: bytes,
    signature: str | None,
    event_id: str | None,
) -> WebhookProcessResult:
    """Verify raw-body HMAC, persist the delivery, apply commercial effects at most once."""
    now = datetime.now(timezone.utc)
    body_hash = sha256_hex(body)
    if not signature:
        row = _persist_unverified(
            db,
            provider_name=provider.name,
            body_hash=body_hash,
            event_id=event_id,
            reason="MISSING_SIGNATURE",
            now=now,
            signature_present=False,
        )
        _audit(db, None, row, "webhook_received", "FAILED", "Missing X-Razorpay-Signature")
        return WebhookProcessResult(
            http_status=400,
            processing_status=WebhookProcessingStatus.FAILED,
            webhook_ref=row.ref_id,
            failure_reason="MISSING_SIGNATURE",
        )

    try:
        envelope = provider.verify_webhook(payload=body, signature=signature)
    except PaymentProviderError as exc:
        if exc.code == "not_configured":
            return WebhookProcessResult(
                http_status=503,
                processing_status=WebhookProcessingStatus.FAILED,
                failure_reason="NOT_CONFIGURED",
            )
        reason = "INVALID_SIGNATURE" if exc.code == "invalid_signature" else "INVALID_PAYLOAD"
        status = 400
        row = _persist_unverified(
            db,
            provider_name=provider.name,
            body_hash=body_hash,
            event_id=event_id,
            reason=reason,
            now=now,
            signature_present=True,
        )
        _audit(db, None, row, "webhook_received", "FAILED", f"Webhook rejected: {reason}")
        return WebhookProcessResult(
            http_status=status,
            processing_status=WebhookProcessingStatus.FAILED,
            webhook_ref=row.ref_id,
            failure_reason=reason,
        )

    envelope.provider_event_id = event_id or f"sha256:{body_hash}"
    _audit(
        db,
        None,
        None,
        "webhook_signature_verified",
        "VERIFIED_SIGNATURE",
        f"Signature valid for {envelope.event_type}",
        extra={"event_type": envelope.event_type, "provider_event_id": envelope.provider_event_id},
    )

    existing = _existing_event(db, provider.name, envelope.provider_event_id)
    if existing is not None:
        if existing.raw_body_hash == body_hash:
            meta = dict(existing.payload_meta or {})
            meta["delivery_count"] = int(meta.get("delivery_count") or 1) + 1
            existing.payload_meta = meta
            db.flush()
            _audit(
                db,
                existing.session,
                existing,
                "webhook_duplicate",
                "DUPLICATE",
                f"Duplicate provider event {envelope.provider_event_id}",
            )
            return WebhookProcessResult(
                http_status=200,
                processing_status=WebhookProcessingStatus.DUPLICATE,
                webhook_ref=existing.ref_id,
                duplicate=True,
                payment_verified=_already_verified_payment(db, existing),
            )
        conflict = WebhookEvent(
            ref_id=next_numeric_ref_id(db, WebhookEvent, RefPrefix.WEBHOOK),
            provider=provider.name,
            provider_event_id=None,
            event_type=envelope.event_type or "unknown",
            signature_valid=True,
            raw_body_hash=body_hash,
            provider_order_id=envelope.provider_order_id,
            provider_payment_id=envelope.provider_payment_id,
            received_at=now,
            processed_at=now,
            processing_status=WebhookProcessingStatus.FAILED.value,
            failure_reason="EVENT_ID_BODY_CONFLICT",
            payload_meta={
                "claimed_event_id": envelope.provider_event_id,
                "existing_ref": existing.ref_id,
                "existing_hash": existing.raw_body_hash,
            },
        )
        db.add(conflict)
        db.flush()
        _audit(
            db,
            existing.session,
            conflict,
            "webhook_received",
            "FAILED",
            "Provider event id reused with a different body",
        )
        return WebhookProcessResult(
            http_status=409,
            processing_status=WebhookProcessingStatus.FAILED,
            webhook_ref=conflict.ref_id,
            failure_reason="EVENT_ID_BODY_CONFLICT",
        )

    row = WebhookEvent(
        ref_id=next_numeric_ref_id(db, WebhookEvent, RefPrefix.WEBHOOK),
        provider=provider.name,
        provider_event_id=envelope.provider_event_id,
        event_type=envelope.event_type or "unknown",
        signature_valid=True,
        raw_body_hash=body_hash,
        provider_order_id=envelope.provider_order_id,
        provider_payment_id=envelope.provider_payment_id,
        received_at=now,
        processed_at=None,
        processing_status=WebhookProcessingStatus.VERIFIED_SIGNATURE.value,
        failure_reason=None,
        payload_meta={
            **envelope.payload_meta,
            "delivery_count": 1,
            "commercial_effect": envelope.commercial_effect,
        },
    )
    db.add(row)
    db.flush()

    if envelope.event_type not in SUPPORTED_WEBHOOK_EVENTS:
        return _finish(
            db,
            row,
            WebhookProcessingStatus.IGNORED,
            reason=None,
            now=now,
            audit_type="webhook_received",
            decision="IGNORED",
            summary=f"Unsupported event {envelope.event_type}",
        )

    attempt = _attempt_for_order(db, provider.name, envelope.provider_order_id)
    if attempt is None:
        return _finish(
            db,
            row,
            WebhookProcessingStatus.IGNORED,
            reason="UNKNOWN_ORDER",
            now=now,
            audit_type="payment_verification_mismatch",
            decision="IGNORED",
            summary="Webhook order does not match a checkout attempt",
        )

    row.session_id = attempt.session_id
    row.checkout_attempt_id = attempt.id
    payment = _payment_for(db, attempt)
    if payment is None:
        return _finish(
            db,
            row,
            WebhookProcessingStatus.FAILED,
            reason="MISSING_PAYMENT",
            now=now,
            session=attempt.session,
            audit_type="payment_verification_mismatch",
            decision="FAILED",
            summary="Checkout has no payment row",
        )
    row.payment_id = payment.id

    mismatch = _correlation_mismatch(attempt, payment, envelope)
    if mismatch:
        return _finish(
            db,
            row,
            WebhookProcessingStatus.FAILED,
            reason=mismatch,
            now=now,
            session=attempt.session,
            audit_type="payment_verification_mismatch",
            decision="FAILED",
            summary=f"Webhook correlation failed: {mismatch}",
        )

    conflict = _payment_id_conflict(db, payment, envelope.provider_payment_id)
    if conflict:
        return _finish(
            db,
            row,
            WebhookProcessingStatus.FAILED,
            reason="PAYMENT_ID_CONFLICT",
            now=now,
            session=attempt.session,
            audit_type="payment_verification_mismatch",
            decision="FAILED",
            summary="Provider payment id already belongs to another checkout",
        )

    verified = False
    if envelope.commercial_effect == "captured":
        verified = _apply_captured(db, attempt, payment, envelope, now)
        decision = "VERIFIED" if verified or _is_verified(attempt, payment) else "PROCESSED"
        summary = (
            f"Payment captured/paid for {attempt.ref_id}"
            if _is_verified(attempt, payment)
            else f"Captured event applied without new commercial effect for {attempt.ref_id}"
        )
        audit_type = "payment_verified" if verified else "webhook_received"
    elif envelope.commercial_effect == "authorized":
        _apply_authorized(db, attempt, payment, envelope, now)
        decision = "AUTHORIZED"
        summary = f"Payment authorized for {attempt.ref_id}"
        audit_type = "payment_authorized"
    else:
        _apply_failed(db, attempt, payment, envelope, now)
        decision = "FAILED" if not _is_verified(attempt, payment) else "VERIFIED"
        summary = (
            f"Failed event ignored; {attempt.ref_id} stays VERIFIED"
            if _is_verified(attempt, payment)
            else f"Payment failed for {attempt.ref_id}"
        )
        audit_type = "payment_failed"

    row.provider_payment_id = envelope.provider_payment_id or row.provider_payment_id
    return _finish(
        db,
        row,
        WebhookProcessingStatus.PROCESSED,
        reason=None,
        now=now,
        session=attempt.session,
        audit_type=audit_type,
        decision=decision,
        summary=summary,
        payment_verified=_is_verified(attempt, payment),
    )


def confirm_payment_from_provider(
    db: Session,
    provider: PaymentProvider,
    checkout_ref_id: str,
    provider_payment_id: str,
) -> WebhookProcessResult:
    """Optional recovery path. Uses provider fetch, never the browser."""
    from app.layers.checkout.service import get_checkout_attempt

    attempt = get_checkout_attempt(db, checkout_ref_id)
    if attempt is None:
        return WebhookProcessResult(
            http_status=404,
            processing_status=WebhookProcessingStatus.FAILED,
            failure_reason="UNKNOWN_CHECKOUT",
        )
    snapshot = provider.fetch_payment(provider_payment_id)
    envelope = VerifiedWebhookEnvelope(
        provider=snapshot.provider,
        event_type="payment.captured",
        provider_order_id=snapshot.provider_order_id,
        provider_payment_id=snapshot.provider_payment_id,
        amount_minor=snapshot.amount_minor,
        currency=snapshot.currency,
        provider_status=snapshot.status,
        commercial_effect="captured" if snapshot.status in {"captured", "paid"} else "none",
        payload_meta={"source": "fetch_payment"},
    )
    if envelope.commercial_effect != "captured":
        return WebhookProcessResult(
            http_status=409,
            processing_status=WebhookProcessingStatus.FAILED,
            failure_reason="NOT_CAPTURED",
        )
    body = (
        f"fetch:{snapshot.provider_payment_id}:{snapshot.provider_order_id}:"
        f"{snapshot.amount_minor}:{snapshot.status}"
    ).encode("utf-8")
    return process_webhook(
        db,
        _FetchAdapter(provider, envelope),
        body=body,
        signature="fetch",
        event_id=f"fetch:{provider_payment_id}",
    )


def list_webhook_events(db: Session, *, checkout_ref_id: str | None = None) -> list[WebhookEvent]:
    stmt = select(WebhookEvent).order_by(WebhookEvent.received_at.asc())
    if checkout_ref_id:
        stmt = stmt.join(CheckoutAttempt).where(CheckoutAttempt.ref_id == checkout_ref_id)
    return list(db.scalars(stmt).all())


class _FetchAdapter:
    """Reuse HMAC process_webhook for an already-trusted fetch snapshot."""

    name: str

    def __init__(self, inner: PaymentProvider, envelope: VerifiedWebhookEnvelope) -> None:
        self.name = inner.name
        self._envelope = envelope

    def verify_webhook(self, *, payload: bytes, signature: str) -> VerifiedWebhookEnvelope:
        _ = payload, signature
        return self._envelope

    def create_order(self, **kwargs):
        raise PaymentProviderError("not_implemented", "Fetch adapter cannot create orders.")

    def fetch_payment(self, provider_payment_id: str) -> PaymentSnapshot:
        _ = provider_payment_id
        raise PaymentProviderError("not_implemented", "Fetch adapter cannot nest fetches.")


def _persist_unverified(
    db: Session,
    *,
    provider_name: str,
    body_hash: str,
    event_id: str | None,
    reason: str,
    now: datetime,
    signature_present: bool,
) -> WebhookEvent:
    row = WebhookEvent(
        ref_id=next_numeric_ref_id(db, WebhookEvent, RefPrefix.WEBHOOK),
        provider=provider_name,
        provider_event_id=None,
        event_type="unknown",
        signature_valid=False,
        raw_body_hash=body_hash,
        received_at=now,
        processed_at=now,
        processing_status=WebhookProcessingStatus.FAILED.value,
        failure_reason=reason,
        payload_meta={"claimed_event_id": event_id, "signature_present": signature_present},
    )
    db.add(row)
    db.flush()
    return row


def _existing_event(db: Session, provider: str, event_id: str | None) -> WebhookEvent | None:
    if not event_id:
        return None
    return db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == provider,
            WebhookEvent.provider_event_id == event_id,
        )
    )


def _attempt_for_order(db: Session, provider: str, order_id: str | None) -> CheckoutAttempt | None:
    if not order_id:
        return None
    return db.scalar(
        select(CheckoutAttempt)
        .options(
            selectinload(CheckoutAttempt.payments),
            selectinload(CheckoutAttempt.session),
            selectinload(CheckoutAttempt.basket),
        )
        .where(
            CheckoutAttempt.provider == provider,
            CheckoutAttempt.provider_order_id == order_id,
        )
    )


def _payment_for(db: Session, attempt: CheckoutAttempt) -> Payment | None:
    if attempt.payments:
        return attempt.payments[0]
    return db.scalar(select(Payment).where(Payment.checkout_attempt_id == attempt.id))


def _correlation_mismatch(
    attempt: CheckoutAttempt,
    payment: Payment,
    envelope: VerifiedWebhookEnvelope,
) -> str | None:
    if envelope.provider and envelope.provider != attempt.provider:
        return "PROVIDER_MISMATCH"
    if not envelope.provider_order_id or envelope.provider_order_id != attempt.provider_order_id:
        return "ORDER_MISMATCH"
    if payment.provider_order_id and envelope.provider_order_id != payment.provider_order_id:
        return "ORDER_MISMATCH"
    if envelope.currency is None or envelope.currency != attempt.currency:
        return "CURRENCY_MISMATCH"
    if envelope.currency != EXPECTED_CURRENCY:
        return "CURRENCY_MISMATCH"
    if envelope.amount_minor is None or envelope.amount_minor != attempt.amount_minor:
        return "PAYMENT_AMOUNT_MISMATCH"
    if envelope.amount_minor != payment.amount_minor:
        return "PAYMENT_AMOUNT_MISMATCH"
    return None


def _payment_id_conflict(db: Session, payment: Payment, provider_payment_id: str | None) -> bool:
    if not provider_payment_id:
        return False
    other = db.scalar(
        select(Payment).where(
            Payment.provider_payment_id == provider_payment_id,
            Payment.id != payment.id,
        )
    )
    return other is not None


def _is_verified(attempt: CheckoutAttempt, payment: Payment) -> bool:
    return (
        attempt.status == CheckoutAttemptStatus.VERIFIED.value
        or payment.status == PaymentStatus.VERIFIED.value
        or payment.verified_at is not None
    )


def _already_verified_payment(db: Session, row: WebhookEvent) -> bool:
    if row.payment_id is None:
        return False
    payment = db.get(Payment, row.payment_id)
    return payment is not None and payment.status == PaymentStatus.VERIFIED.value


def _stamp_payment_id(payment: Payment, provider_payment_id: str | None) -> None:
    if provider_payment_id:
        payment.provider_payment_id = provider_payment_id


def _apply_captured(
    db: Session,
    attempt: CheckoutAttempt,
    payment: Payment,
    envelope: VerifiedWebhookEnvelope,
    now: datetime,
) -> bool:
    _stamp_payment_id(payment, envelope.provider_payment_id)
    if _is_verified(attempt, payment):
        db.flush()
        return False
    payment.status = PaymentStatus.VERIFIED.value
    payment.verified_at = now
    attempt.status = CheckoutAttemptStatus.VERIFIED.value
    attempt.updated_at = now
    payment.updated_at = now
    if attempt.basket is not None:
        attempt.basket.status = CheckoutState.VERIFIED.value
    db.flush()
    return True


def _apply_authorized(
    db: Session,
    attempt: CheckoutAttempt,
    payment: Payment,
    envelope: VerifiedWebhookEnvelope,
    now: datetime,
) -> None:
    _stamp_payment_id(payment, envelope.provider_payment_id)
    if _is_verified(attempt, payment):
        db.flush()
        return
    if payment.status == PaymentStatus.FAILED.value:
        db.flush()
        return
    payment.status = PaymentStatus.AUTHORIZED.value
    payment.updated_at = now
    if attempt.status not in {
        CheckoutAttemptStatus.VERIFIED.value,
        CheckoutAttemptStatus.FAILED.value,
    }:
        attempt.status = CheckoutAttemptStatus.VERIFICATION_PENDING.value
        attempt.updated_at = now
    db.flush()


def _apply_failed(
    db: Session,
    attempt: CheckoutAttempt,
    payment: Payment,
    envelope: VerifiedWebhookEnvelope,
    now: datetime,
) -> None:
    _stamp_payment_id(payment, envelope.provider_payment_id)
    if _is_verified(attempt, payment):
        db.flush()
        return
    payment.status = PaymentStatus.FAILED.value
    payment.verified_at = None
    payment.updated_at = now
    attempt.status = CheckoutAttemptStatus.FAILED.value
    attempt.updated_at = now
    db.flush()


def _finish(
    db: Session,
    row: WebhookEvent,
    status: WebhookProcessingStatus,
    *,
    reason: str | None,
    now: datetime,
    session: ShoppingSession | None = None,
    audit_type: str,
    decision: str,
    summary: str,
    payment_verified: bool = False,
) -> WebhookProcessResult:
    row.processing_status = status.value
    row.failure_reason = reason
    row.processed_at = now
    db.flush()
    _audit(db, session or row.session, row, audit_type, decision, summary)
    return WebhookProcessResult(
        http_status=200,
        processing_status=status,
        webhook_ref=row.ref_id,
        failure_reason=reason,
        payment_verified=payment_verified,
    )


def _audit(
    db: Session,
    session: ShoppingSession | None,
    webhook: WebhookEvent | None,
    event_type: str,
    decision: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "webhook_ref": None if webhook is None else webhook.ref_id,
        "processing_status": None if webhook is None else webhook.processing_status,
        "failure_reason": None if webhook is None else webhook.failure_reason,
        **(extra or {}),
    }
    evidence = record_evidence(
        db,
        session=session,
        kind=EvidenceKind.WEBHOOK.value,
        summary=summary,
        payload=payload,
    )
    record_audit(
        db,
        session=session,
        actor=Actor.SYSTEM.value,
        event_type=event_type,
        decision=decision,
        evidence_ref_ids=[evidence.ref_id],
        payload=payload,
    )
