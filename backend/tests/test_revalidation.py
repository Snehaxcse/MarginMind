"""Final revalidation: approval is not success."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline import process_customer_message
from app.engines.policy import validate_action
from app.layers.approval import (
    approve,
    create_approval_request,
    version_approval_covers,
)
from app.layers.basket import (
    build_complete_looks,
    create_basket,
    get_basket,
    latest_basket_for_session,
    set_items,
    version_label,
)
from app.layers.catalogue import get_variant_by_sku, is_available, set_on_hand_quantity
from app.layers.revalidation import (
    accept_oos_replacement,
    list_revalidations,
    propose_oos_replacement,
    reject_oos_replacement,
    revalidate_approved_basket,
)
from app.layers.session import create_session
from app.models import Offer
from app.providers.llm.stub import HERO_UTTERANCE, StubLLMProvider
from app.schemas.action import ProposedAction
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.revalidation import RevalidationResult
from app.schemas.vocabulary import (
    BoundedAction,
    BudgetType,
    CheckStatus,
    FrictionType,
    PolicyVerdict,
    RevalidationCheckName,
    RevalidationStatus,
)

HERO = ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]
TROUSER_SKUS = ["SKU-004-M", "SKU-004-L", "SKU-005-M", "SKU-006-M", "SKU-006-L"]


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500") -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal="complete_outfit",
        fit_preferences=["relaxed_waist"],
        occasion="farewell",
        usual_size="M",
    )


def _named(result: RevalidationResult, name: RevalidationCheckName):
    return next(item for item in result.checks if item.name is name)


def _approve(
    db: Session,
    shopping,
    skus: list[str],
    *,
    offer_ref_id: str | None = None,
    action_ref_id: str = "ACT-CHK",
):
    basket = set_items(db, create_basket(db, shopping), skus)
    snapshot = {"offer_ref_id": offer_ref_id} if offer_ref_id else None
    request = create_approval_request(
        db, shopping, basket, action_ref_id=action_ref_id, snapshot=snapshot
    )
    approve(db, request.ref_id)
    return basket, request


def test_revalidation_status_vocabulary_is_closed() -> None:
    assert set(RevalidationStatus) == {
        RevalidationStatus.PASS,
        RevalidationStatus.FAILED,
        RevalidationStatus.STOPPED,
    }
    with pytest.raises(ValidationError):
        RevalidationResult(
            session_ref_id="SES-001",
            approval_ref_id="APR-001",
            status="OKAY",  # type: ignore[arg-type]
            validated_at=datetime.now(timezone.utc),
        )


def test_unchanged_approved_basket_passes(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    basket, request = _approve(db, shopping, HERO)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    assert result.status is RevalidationStatus.PASS
    assert result.ref_id and result.ref_id.startswith("REVAL-")
    assert result.failure_reasons == []
    assert _named(result, RevalidationCheckName.SKU_EXISTS).status is CheckStatus.PASS
    assert _named(result, RevalidationCheckName.INVENTORY_AVAILABLE).status is CheckStatus.PASS
    assert _named(result, RevalidationCheckName.PRICE_UNCHANGED).status is CheckStatus.PASS
    assert _named(result, RevalidationCheckName.HARD_BUDGET).status is CheckStatus.PASS
    assert _named(result, RevalidationCheckName.CUSTOMER_APPROVAL_VALID).status is CheckStatus.PASS
    assert _named(result, RevalidationCheckName.BASKET_VERSION_VALID).status is CheckStatus.PASS
    frozen = get_basket(db, basket.ref_id, version=1)
    assert [item.variant.ref_id for item in frozen.items] == HERO


def test_pending_approval_is_not_enough(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), HERO)
    pending = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    result = revalidate_approved_basket(db, shopping, pending.ref_id, intent=_hard())
    assert result.status is RevalidationStatus.STOPPED
    assert _named(result, RevalidationCheckName.CUSTOMER_APPROVAL_VALID).status is CheckStatus.FAIL


def test_stale_approval_stops(db: Session) -> None:
    shopping = _session(db)
    v1, request = _approve(db, shopping, ["SKU-004-M"])
    v2 = set_items(db, v1, ["SKU-004-M", "SKU-007-M"])
    assert v2.version == 2
    result = revalidate_approved_basket(db, shopping, request.ref_id, basket=v2, intent=_hard())
    assert result.status is RevalidationStatus.STOPPED
    assert "STALE_APPROVAL" in result.failure_reasons or "BASKET_VERSION_MISMATCH" in result.failure_reasons
    assert version_approval_covers(db, shopping, v2, action_ref_id="ACT-CHK") is False
    assert version_approval_covers(db, shopping, v1, action_ref_id="ACT-CHK") is True


def test_oos_fails_and_does_not_mutate_approved_basket(db: Session) -> None:
    shopping = _session(db)
    basket, request = _approve(db, shopping, HERO)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=_hard())
    assert result.status is RevalidationStatus.FAILED
    assert "OUT_OF_STOCK" in result.failure_reasons
    frozen = get_basket(db, basket.ref_id, version=1)
    assert [item.variant.ref_id for item in frozen.items] == HERO
    assert version_approval_covers(db, shopping, frozen, action_ref_id="ACT-CHK") is True
    assert latest_basket_for_session(db, shopping).version == 1


def test_price_change_fails_without_silent_update(db: Session) -> None:
    shopping = _session(db)
    basket, request = _approve(db, shopping, HERO)
    variant = get_variant_by_sku(db, "SKU-007-M")
    variant.price_override = Decimal("899.00")
    db.flush()
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=_hard())
    assert result.status is RevalidationStatus.FAILED
    assert "PRICE_CHANGED" in result.failure_reasons
    frozen = get_basket(db, basket.ref_id, version=1)
    line = next(item for item in frozen.items if item.variant.ref_id == "SKU-007-M")
    assert line.unit_price_snapshot == Decimal("749.00")


def test_offer_expiry_stops(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, ["SKU-001-M"], offer_ref_id="OFR-002")
    offer = db.scalar(select(Offer).where(Offer.ref_id == "OFR-002"))
    assert offer is not None
    offer.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    offer.is_active = False
    db.flush()
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=_hard())
    assert result.status is RevalidationStatus.STOPPED
    assert any(
        code in result.failure_reasons
        for code in ("OFFER_INACTIVE", "OFFER_EXPIRED", "UNKNOWN_OFFER")
    )


def test_hard_budget_is_rechecked(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    tight = _hard("2000")
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=tight)
    assert result.status is RevalidationStatus.FAILED
    assert "HARD_BUDGET_VIOLATION" in result.failure_reasons
    assert _named(result, RevalidationCheckName.HARD_BUDGET).status is CheckStatus.FAIL


def test_policy_is_rechecked_against_live_catalogue(db: Session) -> None:
    shopping = _session(db)
    _basket, request = _approve(db, shopping, HERO)
    variant = get_variant_by_sku(db, "SKU-007-M")
    variant.product.is_active = False
    db.flush()
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=_hard())
    assert result.status is not RevalidationStatus.PASS
    assert _named(result, RevalidationCheckName.PRODUCT_ACTIVE).status is CheckStatus.FAIL


def test_oos_rescue_proposes_real_in_stock_under_budget(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    basket, request = _approve(db, shopping, HERO)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is not None
    assert proposal.requires_customer_approval is True
    assert proposal.candidate_sku != "SKU-004-M"
    assert proposal.candidate_sku.startswith("SKU-")
    assert is_available(db, proposal.candidate_sku)
    assert get_variant_by_sku(db, proposal.candidate_sku) is not None
    assert proposal.hard_budget_pass is True
    assert proposal.projected_total <= Decimal("2500.00")
    assert proposal.candidate_sku != "SKU-005-M"
    frozen = get_basket(db, basket.ref_id, version=1)
    assert [item.variant.ref_id for item in frozen.items] == HERO


def test_accept_replacement_forks_new_version_and_requires_new_approval(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    basket, request = _approve(db, shopping, HERO)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is not None
    decision = accept_oos_replacement(db, shopping, proposal, intent)
    assert decision.accepted is True
    assert decision.requires_customer_approval is True
    assert decision.new_basket_version == 2
    original = get_basket(db, basket.ref_id, version=1)
    replacement = get_basket(db, basket.ref_id, version=2)
    assert [item.variant.ref_id for item in original.items] == HERO
    assert proposal.candidate_sku in [item.variant.ref_id for item in replacement.items]
    assert "SKU-004-M" not in [item.variant.ref_id for item in replacement.items]
    assert version_approval_covers(db, shopping, replacement, action_ref_id="ACT-CHK") is False
    assert version_approval_covers(db, shopping, original, action_ref_id="ACT-CHK") is True
    stale = revalidate_approved_basket(db, shopping, request.ref_id, basket=replacement, intent=intent)
    assert stale.status is RevalidationStatus.STOPPED


def test_rejected_replacement_does_not_proceed(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    basket, request = _approve(db, shopping, HERO)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is not None
    decision = reject_oos_replacement(db, shopping, proposal)
    assert decision.stopped is True
    assert decision.accepted is False
    assert latest_basket_for_session(db, shopping).version == 1
    assert get_basket(db, basket.ref_id, version=2) is None
    assert [item.variant.ref_id for item in get_basket(db, basket.ref_id, version=1).items] == HERO


def test_no_valid_replacement_stops(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _basket, request = _approve(db, shopping, HERO)
    for sku in TROUSER_SKUS:
        set_on_hand_quantity(db, sku, 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    assert result.status is RevalidationStatus.FAILED
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is None


def test_repeated_unchanged_revalidation_is_idempotent(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _basket, request = _approve(db, shopping, HERO)
    first = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    second = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    assert first.ref_id == second.ref_id
    assert second.reused is True
    assert first.status is RevalidationStatus.PASS
    rows = list_revalidations(db, shopping)
    assert len(rows) == 1
    assert latest_basket_for_session(db, shopping).version == 1


def test_hero_oos_end_to_end(db: Session) -> None:
    shopping = _session(db)
    extracted = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=HERO_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert extracted.ok is True
    intent = extracted.extraction.intent
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id)
    chosen = next(
        (look for look in looks if sorted(look.skus) == HERO),
        looks[0],
    )
    assert chosen.subtotal <= Decimal("2500")
    basket = set_items(db, create_basket(db, shopping), chosen.skus)
    policy = validate_action(
        db,
        shopping,
        ProposedAction(
            ref_id="ACT-CHK",
            session_ref_id=shopping.ref_id,
            friction_type=FrictionType.CHECKOUT_HESITATION,
            action=BoundedAction.REQUEST_CHECKOUT,
            reason="Customer wants to check out the approved look.",
            evidence_ref_ids=[extracted.evidence_ref_id or "EVD-001"],
            candidate_skus=list(chosen.skus),
            confidence=Decimal("0.92"),
            what="Request checkout",
            why="Purchase plan is complete",
            fix="Require exact-version approval, then revalidate.",
        ),
        intent=intent,
    )
    assert policy.decision is PolicyVerdict.APPROVAL_REQUIRED
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    approve(db, request.ref_id)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    assert result.status is RevalidationStatus.FAILED
    assert "OUT_OF_STOCK" in result.failure_reasons
    original = get_basket(db, basket.ref_id, version=1)
    assert sorted(item.variant.ref_id for item in original.items) == sorted(chosen.skus)
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is not None
    assert is_available(db, proposal.candidate_sku)
    assert proposal.projected_total <= intent.budget.amount
    assert proposal.requires_customer_approval is True
    decision = accept_oos_replacement(db, shopping, proposal, intent)
    assert decision.accepted is True
    assert decision.new_basket_version != 1
    replacement = get_basket(db, basket.ref_id, version=decision.new_basket_version)
    assert version_approval_covers(db, shopping, replacement, action_ref_id="ACT-CHK") is False
    assert decision.requires_customer_approval is True
    stale = revalidate_approved_basket(db, shopping, request.ref_id, basket=replacement, intent=intent)
    assert stale.status is RevalidationStatus.STOPPED
    assert get_basket(db, basket.ref_id, version=1) is not None
    assert latest_basket_for_session(db, shopping).id != original.id

