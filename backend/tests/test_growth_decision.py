"""Growth Decision Engine: proposed actions only. No policy, no execution, no LLM."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.engines.growth_decision import (
    DEMO_ATTACH_SKU,
    list_agent_actions,
    propose_growth_action,
)
from app.layers.basket import create_basket, latest_basket_for_session, set_items, version_label
from app.layers.catalogue import get_variant_by_sku, is_available
from app.layers.friction import diagnose_friction, record_session_signal
from app.layers.session import create_session
from app.schemas.action import ProposedAction
from app.schemas.friction import SessionSignalInput
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.vocabulary import (
    ActionStatus,
    BoundedAction,
    BudgetType,
    FrictionType,
    SessionEventType,
)

_SEEDED_OFFERS = {"OFR-001", "OFR-002", "OFR-003"}
_CLOSED_ACTIONS = set(BoundedAction)


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500", *, goal: str | None = "complete_outfit", size: str | None = None) -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal=goal,
        usual_size=size,
        fit_preferences=["relaxed_waist"],
        occasion="farewell",
    )


def _signal(shopping, db: Session, event_type: SessionEventType, **payload) -> None:
    record_session_signal(
        db, session=shopping, signal=SessionSignalInput(event_type=event_type, **payload)
    )


def _basket_fingerprint(db: Session, shopping) -> tuple[str | None, tuple[str, ...]]:
    basket = latest_basket_for_session(db, shopping)
    if basket is None:
        return None, ()
    skus = tuple(item.variant.ref_id for item in basket.items if item.variant)
    return version_label(basket), skus


def _assert_proposal(proposal: ProposedAction, db: Session) -> None:
    assert proposal.action in _CLOSED_ACTIONS
    assert proposal.status is ActionStatus.PROPOSED
    assert proposal.requires_policy_check is True
    assert proposal.evidence_ref_ids
    assert proposal.ref_id and proposal.ref_id.startswith("ACT-")
    assert proposal.fix
    for sku in proposal.candidate_skus:
        assert get_variant_by_sku(db, sku) is not None
        assert sku.startswith("SKU-")
    if proposal.offer_ref_id is not None:
        assert proposal.offer_ref_id in _SEEDED_OFFERS


def test_action_vocabulary_is_closed() -> None:
    assert set(BoundedAction) == {
        BoundedAction.RECOMMEND,
        BoundedAction.BUILD_BASKET,
        BoundedAction.GUIDE_CONFIDENCE,
        BoundedAction.SIMPLIFY_CHOICES,
        BoundedAction.FIND_ALTERNATIVE,
        BoundedAction.REBUILD_BASKET,
        BoundedAction.APPLY_AUTHORIZED_OFFER,
        BoundedAction.NO_UPSELL,
        BoundedAction.REQUEST_CHECKOUT,
        BoundedAction.STOP,
    }
    with pytest.raises(ValidationError):
        ProposedAction(
            session_ref_id="SES-001",
            friction_type=FrictionType.NONE,
            action="GIVE_DISCOUNT",  # type: ignore[arg-type]
            reason="nope",
            evidence_ref_ids=["EVD-001"],
            confidence=Decimal("0.9"),
            what="nope",
            why="nope",
            fix="nope",
        )


def test_fit_uncertainty_proposes_guide_confidence(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    before = _basket_fingerprint(db, shopping)
    for _ in range(3):
        _signal(shopping, db, SessionEventType.SIZE_GUIDE_OPENED, sku="SKU-004-M")
    _signal(
        shopping,
        db,
        SessionEventType.PRODUCT_COMPARED,
        sku="SKU-004-M",
        sku_b="SKU-001-M",
        dimension="fit",
    )
    _signal(shopping, db, SessionEventType.FIT_QUESTION_ASKED, text="Will the waist bunch?")
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.FIT_UNCERTAINTY
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.GUIDE_CONFIDENCE
    assert proposal.requires_customer_approval is False
    assert proposal.offer_ref_id is None
    assert proposal.action is not BoundedAction.APPLY_AUTHORIZED_OFFER
    _assert_proposal(proposal, db)
    assert _basket_fingerprint(db, shopping) == before
    stored = list_agent_actions(db, shopping)
    assert stored[0].action == BoundedAction.GUIDE_CONFIDENCE.value
    assert stored[0].status == ActionStatus.PROPOSED.value


def test_no_upsell_when_accessory_exceeds_hard_budget(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2000")
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    before = _basket_fingerprint(db, shopping)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(
        db, shopping, intent=intent, diagnosis=diagnosis, upsell_sku=DEMO_ATTACH_SKU
    )
    assert proposal.action is BoundedAction.NO_UPSELL
    assert "HARD_BUDGET_VIOLATION" in proposal.reason_codes
    assert proposal.potential_revenue_not_pursued == Decimal("499.00")
    assert proposal.offer_ref_id is None
    _assert_proposal(proposal, db)
    assert _basket_fingerprint(db, shopping) == before


def test_budget_mismatch_rebuilds_with_real_sku(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-012-OS"])
    before = _basket_fingerprint(db, shopping)
    _signal(shopping, db, SessionEventType.BASKET_OVER_HARD_BUDGET)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.BUDGET_MISMATCH
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.REBUILD_BASKET
    assert proposal.requires_customer_approval is True
    assert "SKU-011-OS" in proposal.candidate_skus
    assert proposal.offer_ref_id is None
    _assert_proposal(proposal, db)
    assert _basket_fingerprint(db, shopping) == before


def test_budget_impossible_stops(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("200")
    set_items(db, create_basket(db, shopping), ["SKU-011-OS"])
    _signal(shopping, db, SessionEventType.BASKET_OVER_HARD_BUDGET)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action in {BoundedAction.STOP, BoundedAction.NO_UPSELL}
    assert proposal.offer_ref_id is None
    _assert_proposal(proposal, db)


def test_price_hesitation_prefers_cheaper_alternative_before_offer(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    set_items(db, create_basket(db, shopping), ["SKU-002-M"])
    _signal(shopping, db, SessionEventType.PRICE_QUESTION_ASKED, text="Is there something cheaper?")
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.PRICE_HESITATION
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action in {BoundedAction.FIND_ALTERNATIVE, BoundedAction.REBUILD_BASKET}
    assert proposal.action is not BoundedAction.APPLY_AUTHORIZED_OFFER
    assert proposal.offer_ref_id is None
    assert proposal.candidate_skus
    cheaper = get_variant_by_sku(db, proposal.candidate_skus[0])
    original = get_variant_by_sku(db, "SKU-002-M")
    assert cheaper is not None and original is not None
    from app.layers.catalogue import effective_price

    assert effective_price(cheaper) < effective_price(original)
    _assert_proposal(proposal, db)


def test_size_unavailable_finds_alternative(db: Session) -> None:
    shopping = _session(db)
    intent = _hard(size="S")
    _signal(
        shopping,
        db,
        SessionEventType.SIZE_UNAVAILABLE_OBSERVED,
        sku="SKU-004-S",
        size="S",
    )
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.FIND_ALTERNATIVE
    assert proposal.candidate_skus
    for sku in proposal.candidate_skus:
        variant = get_variant_by_sku(db, sku)
        assert variant is not None
        assert is_available(db, sku, 1)
    _assert_proposal(proposal, db)


def test_oos_finds_in_stock_alternative_without_mutating(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M"])
    before = _basket_fingerprint(db, shopping)
    variant = get_variant_by_sku(db, "SKU-004-M")
    assert variant is not None and variant.inventory is not None
    variant.inventory.quantity = 0
    db.flush()
    _signal(shopping, db, SessionEventType.PRODUCT_OOS_OBSERVED, sku="SKU-004-M")
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.OUT_OF_STOCK
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.FIND_ALTERNATIVE
    assert proposal.requires_customer_approval is False
    assert proposal.candidate_skus
    assert "SKU-004-M" not in proposal.candidate_skus
    for sku in proposal.candidate_skus:
        assert is_available(db, sku, 1)
    _assert_proposal(proposal, db)
    assert _basket_fingerprint(db, shopping) == before


def test_choice_overload_simplifies_to_three(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _signal(shopping, db, SessionEventType.CHOICES_SHOWN, choice_count=9)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.SIMPLIFY_CHOICES
    assert 1 <= len(proposal.candidate_skus) <= 3
    _assert_proposal(proposal, db)


def test_basket_incomplete_builds_look(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    set_items(db, create_basket(db, shopping), ["SKU-004-M"])
    before = _basket_fingerprint(db, shopping)
    _signal(shopping, db, SessionEventType.BASKET_UPDATED, sku="SKU-004-M")
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.BUILD_BASKET
    assert proposal.requires_customer_approval is True
    assert len(proposal.candidate_skus) >= 2
    _assert_proposal(proposal, db)
    assert _basket_fingerprint(db, shopping) == before


def test_catalogue_gap_stops(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _signal(shopping, db, SessionEventType.CHOICES_SHOWN, choice_count=0)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.CATALOGUE_GAP
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.STOP
    assert proposal.candidate_skus == []
    _assert_proposal(proposal, db)


def test_unknown_stops_without_guessing(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _signal(shopping, db, SessionEventType.PRODUCT_VIEWED, sku="SKU-007-M")
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.UNKNOWN
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.STOP
    assert proposal.offer_ref_id is None
    _assert_proposal(proposal, db)
