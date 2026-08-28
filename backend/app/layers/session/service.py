"""Shopping session timeline. Audit trail, not event sourcing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.models import Customer, Merchant, SessionEvent, ShoppingSession


class SessionServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def create_session(
    db: Session,
    *,
    merchant_ref_id: str,
    customer_ref_id: str,
) -> ShoppingSession:
    merchant = db.scalar(select(Merchant).where(Merchant.ref_id == merchant_ref_id))
    customer = db.scalar(select(Customer).where(Customer.ref_id == customer_ref_id))
    if merchant is None or not merchant.is_active:
        raise SessionServiceError("unknown_merchant", f"Merchant {merchant_ref_id} is not usable.")
    if customer is None:
        raise SessionServiceError("unknown_customer", f"Customer {customer_ref_id} was not found.")
    if customer.merchant_id != merchant.id:
        raise SessionServiceError("customer_merchant_mismatch", "Customer does not belong to merchant.")
    row = ShoppingSession(
        ref_id=next_numeric_ref_id(db, ShoppingSession, RefPrefix.SESSION),
        merchant_id=merchant.id,
        customer_id=customer.id,
        status="open",
    )
    db.add(row)
    db.flush()
    return row


def get_session_by_ref_id(db: Session, ref_id: str) -> ShoppingSession | None:
    return db.scalar(select(ShoppingSession).where(ShoppingSession.ref_id == ref_id))


def require_session(db: Session, ref_id: str) -> ShoppingSession:
    row = get_session_by_ref_id(db, ref_id)
    if row is None:
        raise SessionServiceError("unknown_session", f"Session {ref_id} was not found.")
    return row


def append_session_event(
    db: Session,
    *,
    session: ShoppingSession,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    evidence_ref_ids: list[str] | None = None,
) -> SessionEvent:
    body = dict(payload or {})
    body.setdefault("session_ref_id", session.ref_id)
    event = SessionEvent(
        ref_id=next_numeric_ref_id(db, SessionEvent, RefPrefix.EVENT),
        session_id=session.id,
        event_type=event_type,
        actor=actor,
        payload=body,
        evidence_ref_ids=list(evidence_ref_ids or []),
    )
    db.add(event)
    db.flush()
    return event


def list_session_events(db: Session, session: ShoppingSession) -> list[SessionEvent]:
    return list(
        db.scalars(
            select(SessionEvent)
            .where(SessionEvent.session_id == session.id)
            .order_by(SessionEvent.created_at.asc(), SessionEvent.ref_id.asc())
        ).all()
    )
