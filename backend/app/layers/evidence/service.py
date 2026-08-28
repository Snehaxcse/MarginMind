"""Evidence records for reconstructable decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.models import Evidence, ShoppingSession


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
