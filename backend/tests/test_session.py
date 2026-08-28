"""Session create, lookup, and chronological events."""

from sqlalchemy.orm import Session

from app.layers.session import append_session_event, create_session, list_session_events, require_session
from app.schemas.vocabulary import Actor, SessionEventType


def test_create_and_get_session(db: Session) -> None:
    shopping = create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")
    assert shopping.ref_id.startswith("SES-")
    found = require_session(db, shopping.ref_id)
    assert found.id == shopping.id


def test_events_are_chronological_with_stable_ids(db: Session) -> None:
    shopping = create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")
    first = append_session_event(
        db,
        session=shopping,
        event_type=SessionEventType.CUSTOMER_MESSAGE.value,
        actor=Actor.CUSTOMER.value,
        payload={"text": "one"},
        evidence_ref_ids=[],
    )
    second = append_session_event(
        db,
        session=shopping,
        event_type=SessionEventType.INTENT_EXTRACTED.value,
        actor=Actor.SYSTEM.value,
        payload={"text": "two"},
        evidence_ref_ids=[],
    )
    assert first.ref_id.startswith("EVT-")
    assert second.ref_id.startswith("EVT-")
    ordered = list_session_events(db, shopping)
    assert [item.ref_id for item in ordered] == [first.ref_id, second.ref_id]
    assert ordered[0].payload["session_ref_id"] == shopping.ref_id
