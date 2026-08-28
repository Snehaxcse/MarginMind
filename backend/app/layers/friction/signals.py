"""Record structured session signals as events + evidence.

Does not diagnose friction. Event types are the closed SessionEventType set.
"""

from __future__ import annotations

from app.layers.evidence import record_evidence
from app.layers.session import append_session_event
from app.models import SessionEvent, ShoppingSession
from app.schemas.friction import SessionSignalInput
from app.schemas.vocabulary import Actor, EvidenceKind, SessionEventType
from sqlalchemy.orm import Session

_CUSTOMER_EVENTS = {
    SessionEventType.CUSTOMER_MESSAGE,
    SessionEventType.PRODUCT_VIEWED,
    SessionEventType.SIZE_GUIDE_OPENED,
    SessionEventType.PRODUCT_COMPARED,
    SessionEventType.RECOMMENDATION_REJECTED,
    SessionEventType.REJECTION_REASON_RECORDED,
    SessionEventType.FIT_QUESTION_ASKED,
    SessionEventType.PRICE_QUESTION_ASKED,
    SessionEventType.CHECKOUT_STARTED,
    SessionEventType.CHECKOUT_ABANDONED,
}


def record_session_signal(
    db: Session,
    *,
    session: ShoppingSession,
    signal: SessionSignalInput,
) -> SessionEvent:
    payload = signal.model_dump(exclude_none=True, mode="json")
    payload["event_type"] = signal.event_type.value
    evidence = record_evidence(
        db,
        session=session,
        kind=EvidenceKind.SESSION_SIGNAL.value,
        summary=_signal_summary(signal),
        payload=payload,
    )
    actor = Actor.CUSTOMER.value if signal.event_type in _CUSTOMER_EVENTS else Actor.SYSTEM.value
    return append_session_event(
        db,
        session=session,
        event_type=signal.event_type.value,
        actor=actor,
        payload=payload,
        evidence_ref_ids=[evidence.ref_id],
    )


def _signal_summary(signal: SessionSignalInput) -> str:
    parts = [signal.event_type.value]
    if signal.sku:
        parts.append(signal.sku)
    if signal.sku_b:
        parts.append(signal.sku_b)
    if signal.size:
        parts.append(f"size={signal.size}")
    if signal.reason:
        parts.append(signal.reason)
    if signal.dimension:
        parts.append(signal.dimension)
    if signal.choice_count is not None:
        parts.append(f"choices={signal.choice_count}")
    if signal.text:
        parts.append(signal.text[:80])
    return " ".join(parts)[:240]
