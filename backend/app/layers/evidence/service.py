"""Evidence records for reconstructable decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.models import AuditEvent, Evidence, ShoppingSession


def record_evidence(
    db: Session,
    *,
    session: ShoppingSession | None,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> Evidence:
    row = Evidence(
        ref_id=next_numeric_ref_id(db, Evidence, RefPrefix.EVIDENCE),
        session_id=None if session is None else session.id,
        kind=kind,
        summary=summary,
        payload=payload,
    )
    db.add(row)
    db.flush()
    return row


def record_audit(
    db: Session,
    *,
    session: ShoppingSession | None,
    actor: str,
    event_type: str,
    decision: str | None = None,
    evidence_ref_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append-only audit row. Callers must not update existing events."""
    row = AuditEvent(
        ref_id=next_numeric_ref_id(db, AuditEvent, RefPrefix.AUDIT),
        session_id=None if session is None else session.id,
        actor=actor,
        event_type=event_type,
        decision=decision,
        evidence_ref_ids=list(evidence_ref_ids or []),
        payload=dict(payload or {}),
    )
    db.add(row)
    db.flush()
    return row


def record_customer_message(
    db: Session,
    *,
    session: ShoppingSession,
    text: str,
) -> Evidence:
    return record_evidence(
        db,
        session=session,
        kind="customer_message",
        summary=text[:240],
        payload={"source": "shopper", "content": text},
    )
