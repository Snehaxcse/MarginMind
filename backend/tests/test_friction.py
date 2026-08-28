"""Rule-based friction diagnosis. No GDE, no Gemini, no actions."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.layers.basket import create_basket, set_items
from app.layers.catalogue import get_variant_by_sku
from app.layers.friction import (
    diagnose_friction,
    list_friction_diagnoses,
    record_session_signal,
)
from app.layers.session import create_session, list_session_events
from app.schemas.friction import FrictionDiagnosisResult, SessionSignalInput
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.vocabulary import BoundedAction, BudgetType, FrictionType, SessionEventType

_CLOSED_FRICTION = {
    FrictionType.FIT_UNCERTAINTY,
    FrictionType.STYLE_UNCERTAINTY,
    FrictionType.COLOUR_UNCERTAINTY,
    FrictionType.BUDGET_MISMATCH,
    FrictionType.PRICE_HESITATION,
    FrictionType.SIZE_UNAVAILABLE,
    FrictionType.OUT_OF_STOCK,
    FrictionType.CHOICE_OVERLOAD,
    FrictionType.BASKET_INCOMPLETE,
    FrictionType.CATALOGUE_GAP,
    FrictionType.CHECKOUT_HESITATION,
    FrictionType.NONE,
    FrictionType.UNKNOWN,
}
_ACTIONS = {item.value for item in BoundedAction}


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500", *, goal: str | None = "complete_outfit", size: str | None = None) -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal=goal,
        usual_size=size,
        fit_preferences=["relaxed_waist"],
    )


def _signal(shopping, db: Session, event_type: SessionEventType, **payload) -> None:
    record_session_signal(
        db, session=shopping, signal=SessionSignalInput(event_type=event_type, **payload)
    )


def _assert_evidence_only(result) -> None:
    payload = result.primary.model_dump()
    assert result.primary.evidence_ref_ids
    for item in result.diagnoses:
        assert item.evidence_ref_ids
        assert item.friction_type in _CLOSED_FRICTION
    joined = str(payload)
    for action in _ACTIONS:
        assert action not in joined
    assert "fix" not in payload
    assert "proposed_action" not in payload


def test_friction_vocabulary_is_closed() -> None:
    assert set(FrictionType) == _CLOSED_FRICTION
    with pytest.raises(ValidationError):
        FrictionDiagnosisResult(
            friction_type="VIBES",  # type: ignore[arg-type]
            confidence=Decimal("0.9"),
            evidence_ref_ids=["EVT-001"],
            summary="nope",
            why="nope",
            status="active",
        )


def test_hero_fit_uncertainty(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    for _ in range(3):
        _signal(shopping, db, SessionEventType.SIZE_GUIDE_OPENED, product_ref_id="PRD-004", sku="SKU-004-M")
    _signal(
        shopping,
        db,
        SessionEventType.PRODUCT_COMPARED,
        sku="SKU-004-M",
        sku_b="SKU-001-M",
        dimension="fit",
    )
    _signal(
        shopping,
        db,
        SessionEventType.FIT_QUESTION_ASKED,
        text="Will these trousers bunch at the waist on a 5'2\" frame?",
    )
    result = diagnose_friction(db, shopping, intent=intent)
    assert result.primary.friction_type is FrictionType.FIT_UNCERTAINTY
    assert Decimal("0.65") <= result.primary.confidence <= Decimal("0.95")
    assert any(ref.startswith("EVT-") for ref in result.primary.evidence_ref_ids)
    assert result.primary.ref_id and result.primary.ref_id.startswith("FRIC-")
    _assert_evidence_only(result)
    stored = list_friction_diagnoses(db, shopping)
    assert stored[0].friction_type == FrictionType.FIT_UNCERTAINTY.value


def test_budget_mismatch_from_hard_overage(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-012-OS"])
    _signal(shopping, db, SessionEventType.BASKET_OVER_HARD_BUDGET)
    result = diagnose_friction(db, shopping, intent=intent)
    assert result.primary.friction_type is FrictionType.BUDGET_MISMATCH
    assert result.primary.confidence == Decimal("0.92")
    assert "HARD_BUDGET_EXCEEDED" in result.primary.reason_codes
    _assert_evidence_only(result)


def test_price_hesitation_requires_explicit_price_evidence(db: Session) -> None:
    shopping = _session(db)
    _signal(shopping, db, SessionEventType.PRODUCT_VIEWED, sku="SKU-005-M")
    viewed_only = diagnose_friction(db, shopping, intent=_hard())
    assert viewed_only.primary.friction_type is FrictionType.UNKNOWN
    _signal(shopping, db, SessionEventType.PRICE_QUESTION_ASKED, text="Is there a cheaper option?")
    result = diagnose_friction(db, shopping, intent=_hard())
    assert result.primary.friction_type is FrictionType.PRICE_HESITATION
    assert "EXPLICIT_PRICE_QUESTION" in result.primary.reason_codes
    _assert_evidence_only(result)


def test_size_unavailable(db: Session) -> None:
    shopping = _session(db)
    intent = _hard(size="S")
    _signal(
        shopping,
        db,
        SessionEventType.SIZE_UNAVAILABLE_OBSERVED,
        sku="SKU-004-S",
        product_ref_id="PRD-004",
        size="S",
    )
    result = diagnose_friction(db, shopping, intent=intent)
    assert result.primary.friction_type is FrictionType.SIZE_UNAVAILABLE
    _assert_evidence_only(result)


def test_selected_sku_becomes_oos(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M"])
    variant = get_variant_by_sku(db, "SKU-004-M")
    assert variant is not None and variant.inventory is not None
    variant.inventory.quantity = 0
    db.flush()
    _signal(shopping, db, SessionEventType.PRODUCT_OOS_OBSERVED, sku="SKU-004-M")
    result = diagnose_friction(db, shopping, intent=_hard())
    assert result.primary.friction_type is FrictionType.OUT_OF_STOCK
    _assert_evidence_only(result)
    assert basket.items


def test_choice_overload(db: Session) -> None:
    shopping = _session(db)
    _signal(shopping, db, SessionEventType.CHOICES_SHOWN, choice_count=9)
    for _ in range(3):
        _signal(shopping, db, SessionEventType.PRODUCT_COMPARED, sku="SKU-001-M", sku_b="SKU-002-M")
    for _ in range(2):
        _signal(shopping, db, SessionEventType.RECOMMENDATION_REJECTED, sku="SKU-003-M")
    result = diagnose_friction(db, shopping, intent=_hard())
    assert result.primary.friction_type is FrictionType.CHOICE_OVERLOAD
    _assert_evidence_only(result)


def test_basket_incomplete(db: Session) -> None:
    shopping = _session(db)
    set_items(db, create_basket(db, shopping), ["SKU-004-M"])
    _signal(shopping, db, SessionEventType.BASKET_UPDATED, sku="SKU-004-M")
    result = diagnose_friction(db, shopping, intent=_hard())
    assert result.primary.friction_type is FrictionType.BASKET_INCOMPLETE
    _assert_evidence_only(result)


def test_checkout_hesitation(db: Session) -> None:
    shopping = _session(db)
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    _signal(shopping, db, SessionEventType.CHECKOUT_STARTED)
    _signal(shopping, db, SessionEventType.CHECKOUT_ABANDONED)
    result = diagnose_friction(db, shopping, intent=_hard())
    assert result.primary.friction_type is FrictionType.CHECKOUT_HESITATION
    _assert_evidence_only(result)


def test_insufficient_evidence_none_and_unknown(db: Session) -> None:
    shopping = _session(db)
    empty = diagnose_friction(db, shopping, intent=_hard())
    assert empty.primary.friction_type is FrictionType.NONE
    assert empty.primary.evidence_ref_ids
    _signal(shopping, db, SessionEventType.PRODUCT_VIEWED, sku="SKU-007-M")
    weak = diagnose_friction(db, shopping, intent=_hard())
    assert weak.primary.friction_type is FrictionType.UNKNOWN
    assert weak.primary.confidence == Decimal("0.45")


def test_multiple_frictions_ranked(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-012-OS"])
    for _ in range(3):
        _signal(shopping, db, SessionEventType.SIZE_GUIDE_OPENED, sku="SKU-004-M")
    _signal(
        shopping,
        db,
        SessionEventType.FIT_QUESTION_ASKED,
        text="Is the waist relaxed enough?",
    )
    result = diagnose_friction(db, shopping, intent=intent)
    types = [item.friction_type for item in result.diagnoses]
    assert FrictionType.BUDGET_MISMATCH in types
    assert FrictionType.FIT_UNCERTAINTY in types
    assert result.primary.friction_type is FrictionType.BUDGET_MISMATCH
    assert result.primary.confidence >= result.secondary[0].confidence
    _assert_evidence_only(result)


def test_signals_use_stable_event_refs(db: Session) -> None:
    shopping = _session(db)
    _signal(shopping, db, SessionEventType.SIZE_GUIDE_OPENED, sku="SKU-004-M")
    events = list_session_events(db, shopping)
    assert events[0].ref_id.startswith("EVT-")
    assert events[0].evidence_ref_ids[0].startswith("EVD-")
    assert events[0].event_type == SessionEventType.SIZE_GUIDE_OPENED.value
