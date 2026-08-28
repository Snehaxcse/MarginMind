"""Policy Engine: proposal is not permission. Database truth only."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.growth_decision import (
    DEMO_ATTACH_SKU,
    propose_growth_action,
)
from app.engines.policy import list_policy_decisions, validate_action
from app.layers.approval import (
    approval_covers,
    approve,
    create_approval_request,
    reject,
    version_approval_covers,
)
from app.layers.basket import (
    create_basket,
    latest_basket_for_session,
    set_items,
    version_label,
)
from app.layers.catalogue import get_variant_by_sku
from app.layers.friction import diagnose_friction, record_session_signal
from app.layers.session import create_session
from app.models import Offer
from app.schemas.action import ProposedAction
from app.schemas.friction import SessionSignalInput
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.policy import PolicyDecision
from app.schemas.vocabulary import (
    BoundedAction,
    BudgetType,
    CheckStatus,
    FrictionType,
    PolicyCheckName,
    PolicyVerdict,
    SessionEventType,
)

_CLOSED_VERDICTS = {PolicyVerdict.PASS, PolicyVerdict.BLOCK, PolicyVerdict.APPROVAL_REQUIRED}
_CLOSED_CHECKS = set(PolicyCheckName)


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500", *, goal: str | None = "complete_outfit") -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal=goal,
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


def _draft(
    shopping,
    action: BoundedAction,
    *,
    candidate_skus: list[str] | None = None,
    offer_ref_id: str | None = None,
    friction_type: FrictionType = FrictionType.NONE,
    ref_id: str | None = None,
    potential_revenue_not_pursued: Decimal | None = None,
) -> ProposedAction:
    return ProposedAction(
        ref_id=ref_id,
        session_ref_id=shopping.ref_id,
        friction_type=friction_type,
        action=action,
        reason="test proposal",
        evidence_ref_ids=["EVD-000"],
        candidate_skus=candidate_skus or [],
        offer_ref_id=offer_ref_id,
        confidence=Decimal("0.92"),
        potential_revenue_not_pursued=potential_revenue_not_pursued,
        what="what",
        why="why",
        fix="fix",
    )


def _named(result: PolicyDecision, name: PolicyCheckName):
    return next(item for item in result.checks if item.name is name)


def test_policy_verdict_vocabulary_is_closed() -> None:
    assert set(PolicyVerdict) == _CLOSED_VERDICTS
    assert set(PolicyCheckName) == {
        PolicyCheckName.HARD_BUDGET,
        PolicyCheckName.INVENTORY,
        PolicyCheckName.SKU_EXISTS,
        PolicyCheckName.PRODUCT_ACTIVE,
        PolicyCheckName.VARIANT_ACTIVE,
        PolicyCheckName.MARGIN,
        PolicyCheckName.AUTHORIZED_OFFER,
        PolicyCheckName.OFFER_ACTIVE,
        PolicyCheckName.OFFER_ELIGIBILITY,
        PolicyCheckName.OFFER_STACKING,
        PolicyCheckName.MERCHANT_RESTRICTIONS,
        PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED,
        PolicyCheckName.NO_SILENT_BASKET_CHANGE,
    }
    with pytest.raises(ValidationError):
        PolicyDecision(
            session_ref_id="SES-001",
            allowed=True,
            decision="ALLOW_ANYWAY",  # type: ignore[arg-type]
            validated_at=datetime.now(timezone.utc),
        )


def test_guide_confidence_passes_without_approval_or_finance(db: Session) -> None:
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
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.PASS
    assert result.allowed is True
    assert result.requires_customer_approval is False
    assert result.ref_id and result.ref_id.startswith("PDEC-")
    assert _named(result, PolicyCheckName.HARD_BUDGET).status is CheckStatus.NA
    assert _named(result, PolicyCheckName.MARGIN).status is CheckStatus.NA
    assert _named(result, PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED).status is CheckStatus.NA
    assert _basket_fingerprint(db, shopping) == before
    stored = list_policy_decisions(db, shopping)
    assert stored[0].decision == PolicyVerdict.PASS.value


def test_no_upsell_passes_and_records_forgone_revenue(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2000")
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    before = _basket_fingerprint(db, shopping)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(
        db, shopping, intent=intent, diagnosis=diagnosis, upsell_sku=DEMO_ATTACH_SKU
    )
    assert proposal.action is BoundedAction.NO_UPSELL
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.PASS
    assert result.requires_customer_approval is False
    assert proposal.potential_revenue_not_pursued == Decimal("499.00")
    assert _basket_fingerprint(db, shopping) == before
    assert "HARD_BUDGET_VIOLATION" in proposal.reason_codes


def test_stop_passes(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(shopping, BoundedAction.STOP, friction_type=FrictionType.CATALOGUE_GAP),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.PASS
    assert result.allowed is True
    assert result.requires_customer_approval is False


def test_invalid_sku_is_blocked(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.REBUILD_BASKET,
            candidate_skus=["SKU-999-M"],
            friction_type=FrictionType.BUDGET_MISMATCH,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert result.allowed is False
    assert _named(result, PolicyCheckName.SKU_EXISTS).status is CheckStatus.FAIL
    assert "SKU_NOT_FOUND" in result.reason_codes


def test_oos_sku_is_blocked(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.REBUILD_BASKET,
            candidate_skus=["SKU-013-OS"],
            friction_type=FrictionType.OUT_OF_STOCK,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert _named(result, PolicyCheckName.SKU_EXISTS).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.INVENTORY).status is CheckStatus.FAIL
    assert "OUT_OF_STOCK" in result.reason_codes


def test_hard_budget_violation_blocked_even_if_gde_proposes_it(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    over = ["SKU-004-M", "SKU-007-M", "SKU-012-OS"]
    live = sum(
        (get_variant_by_sku(db, sku).product.base_price for sku in over),
        Decimal("0"),
    )
    assert live == Decimal("2647.00")
    proposal = _draft(
        shopping,
        BoundedAction.REBUILD_BASKET,
        candidate_skus=over,
        friction_type=FrictionType.BUDGET_MISMATCH,
        ref_id="ACT-GDE-LIE",
    )
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.BLOCK
    assert result.allowed is False
    assert _named(result, PolicyCheckName.HARD_BUDGET).status is CheckStatus.FAIL
    assert "HARD_BUDGET_VIOLATION" in result.reason_codes
    assert result.resulting_subtotal == Decimal("2647.00")


def test_valid_rebuild_requires_customer_approval(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-012-OS"])
    before = _basket_fingerprint(db, shopping)
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.REBUILD_BASKET
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.APPROVAL_REQUIRED
    assert result.allowed is True
    assert result.requires_customer_approval is True
    assert _named(result, PolicyCheckName.HARD_BUDGET).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.SKU_EXISTS).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.INVENTORY).status is CheckStatus.PASS
    assert result.resulting_subtotal is not None
    assert result.resulting_subtotal <= Decimal("2500.00")
    assert _basket_fingerprint(db, shopping) == before


def test_unauthorized_offer_is_blocked(db: Session) -> None:
    shopping = _session(db)
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-999",
            candidate_skus=["SKU-001-M"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert _named(result, PolicyCheckName.AUTHORIZED_OFFER).status is CheckStatus.FAIL
    assert "UNKNOWN_OFFER" in result.reason_codes


def test_inactive_offer_is_blocked(db: Session) -> None:
    shopping = _session(db)
    offer = db.scalar(select(Offer).where(Offer.ref_id == "OFR-002"))
    assert offer is not None
    offer.is_active = False
    db.flush()
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-002",
            candidate_skus=["SKU-001-M"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert _named(result, PolicyCheckName.OFFER_ACTIVE).status is CheckStatus.FAIL
    assert "OFFER_INACTIVE" in result.reason_codes


def test_expired_offer_is_blocked(db: Session) -> None:
    shopping = _session(db)
    offer = db.scalar(select(Offer).where(Offer.ref_id == "OFR-002"))
    assert offer is not None
    offer.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.flush()
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-002",
            candidate_skus=["SKU-001-M"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert "OFFER_EXPIRED" in result.reason_codes


def test_margin_floor_blocks_offer(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-001",
            candidate_skus=["SKU-004-M", "SKU-007-M"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert _named(result, PolicyCheckName.MARGIN).status is CheckStatus.FAIL
    assert "MARGIN_FLOOR_VIOLATION" in result.reason_codes


def test_discount_max_blocks_offer(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-003",
            candidate_skus=["SKU-012-OS"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert "DISCOUNT_MAX_VIOLATION" in result.reason_codes


def test_offer_stacking_is_blocked(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-002",
            candidate_skus=["SKU-001-M"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
        applied_offer_ref_ids=["OFR-001"],
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert _named(result, PolicyCheckName.OFFER_STACKING).status is CheckStatus.FAIL
    assert "OFFER_STACKING_PROHIBITED" in result.reason_codes


def test_offer_basket_minimum_is_blocked(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-001",
            candidate_skus=["SKU-011-OS"],
            friction_type=FrictionType.PRICE_HESITATION,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.BLOCK
    assert "OFFER_BASKET_MINIMUM" in result.reason_codes


def test_valid_offer_requires_approval(db: Session) -> None:
    shopping = _session(db)
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    before = _basket_fingerprint(db, shopping)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.APPLY_AUTHORIZED_OFFER,
            offer_ref_id="OFR-002",
            candidate_skus=["SKU-001-M"],
            friction_type=FrictionType.PRICE_HESITATION,
            ref_id="ACT-OFFER",
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.APPROVAL_REQUIRED
    assert result.allowed is True
    assert result.requires_customer_approval is True
    assert _named(result, PolicyCheckName.AUTHORIZED_OFFER).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.OFFER_ACTIVE).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.OFFER_ELIGIBILITY).status is CheckStatus.PASS
    assert _named(result, PolicyCheckName.MARGIN).status is CheckStatus.PASS
    assert _basket_fingerprint(db, shopping) == before


def test_exact_basket_version_approval_and_stale_rejection(db: Session) -> None:
    shopping = _session(db)
    v1 = set_items(db, create_basket(db, shopping), ["SKU-004-M"])
    assert v1.version == 1
    request = create_approval_request(db, shopping, v1, action_ref_id="ACT-001")
    assert request.ref_id.startswith("APR-")
    assert request.status == "pending"
    granted = approve(db, request.ref_id)
    assert granted.status == "granted"
    assert version_approval_covers(db, shopping, v1, action_ref_id="ACT-001") is True
    assert approval_covers(db, shopping, action_ref_id="ACT-001", basket=v1) is not None

    v2 = set_items(db, v1, ["SKU-004-M", "SKU-007-M"])
    assert v2.version == 2
    assert version_label(v2) != version_label(v1)
    assert version_approval_covers(db, shopping, v2, action_ref_id="ACT-001") is False
    assert approval_covers(db, shopping, action_ref_id="ACT-001", basket=v2) is None
    assert version_approval_covers(db, shopping, v1, action_ref_id="ACT-001") is True

    checkout = _draft(
        shopping,
        BoundedAction.REQUEST_CHECKOUT,
        friction_type=FrictionType.CHECKOUT_HESITATION,
        ref_id="ACT-001",
    )
    result = validate_action(db, shopping, checkout, intent=_hard())
    assert result.decision is PolicyVerdict.APPROVAL_REQUIRED
    assert result.requires_customer_approval is True


def test_reject_does_not_authorize(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    reject(db, request.ref_id)
    assert version_approval_covers(db, shopping, basket, action_ref_id="ACT-CHK") is False
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.REQUEST_CHECKOUT,
            friction_type=FrictionType.CHECKOUT_HESITATION,
            ref_id="ACT-CHK",
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.APPROVAL_REQUIRED
    assert _basket_fingerprint(db, shopping)[1] == ("SKU-004-M", "SKU-007-M", "SKU-011-OS")


def test_granted_exact_version_allows_checkout_in_principle(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    approve(db, request.ref_id)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.REQUEST_CHECKOUT,
            friction_type=FrictionType.CHECKOUT_HESITATION,
            ref_id="ACT-CHK",
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.PASS
    assert result.requires_customer_approval is False
    assert _named(result, PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED).status is CheckStatus.PASS
    assert _basket_fingerprint(db, shopping)[0] == version_label(basket)


def test_simplify_choices_passes_without_approval(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db,
        shopping,
        _draft(
            shopping,
            BoundedAction.SIMPLIFY_CHOICES,
            candidate_skus=["SKU-001-M", "SKU-002-M", "SKU-004-M"],
            friction_type=FrictionType.CHOICE_OVERLOAD,
        ),
        intent=_hard(),
    )
    assert result.decision is PolicyVerdict.PASS
    assert result.requires_customer_approval is False
    assert _named(result, PolicyCheckName.SKU_EXISTS).status is CheckStatus.PASS


def test_every_check_is_present_and_typed(db: Session) -> None:
    shopping = _session(db)
    result = validate_action(
        db, shopping, _draft(shopping, BoundedAction.STOP), intent=_hard()
    )
    names = {item.name for item in result.checks}
    assert names == _CLOSED_CHECKS
    for item in result.checks:
        assert item.status in {CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.NA}
