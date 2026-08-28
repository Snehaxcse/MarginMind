"""Basket Architect: totals, versions, looks, NO_UPSELL helper."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.ref_ids import basket_version_ref
from app.layers.basket import (
    BasketServiceError,
    add_item,
    build_complete_looks,
    create_basket,
    evaluate_optional_add_on,
    get_basket,
    live_subtotal,
    propose_replacement,
    set_items,
    snapshot_subtotal,
    validate_basket,
    version_label,
)
from app.layers.catalogue import effective_price, get_variant_by_sku
from app.layers.session import create_session
from app.schemas.basket import HARD_BUDGET_VIOLATION, NO_UPSELL
from app.schemas.intent import BudgetIntent, ShopperIntent
from app.schemas.vocabulary import BudgetType, CheckoutState


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str) -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal="complete_outfit",
        fit_preferences=["relaxed_waist"],
        style_preferences=["elegant", "youthful"],
        occasion="farewell",
    )


def test_subtotal_uses_catalogue_price_not_caller(db: Session) -> None:
    shopping = _session(db)
    basket = create_basket(db, shopping)
    basket = add_item(db, basket, "SKU-004-M")
    variant = get_variant_by_sku(db, "SKU-004-M")
    assert variant is not None
    assert snapshot_subtotal(basket) == effective_price(variant)
    assert basket.items[0].unit_price_snapshot == Decimal("1399.00")
    basket.items[0].unit_price_snapshot = Decimal("1.00")
    db.flush()
    assert live_subtotal(db, basket) == Decimal("1399.00")


def test_three_valid_items_can_fail_hard_total(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-012-OS"])
    result = validate_basket(db, basket, _hard("2500"))
    assert live_subtotal(db, basket) == Decimal("2647.00")
    assert result.valid is False
    assert result.hard_budget_pass is False
    assert HARD_BUDGET_VIOLATION in result.reasons


def test_hard_budget_at_limit_passes(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    result = validate_basket(db, basket, _hard("2447"))
    assert result.subtotal == Decimal("2447.00")
    assert result.valid is True
    assert result.hard_budget_pass is True


def test_hard_budget_exceeded_fails(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    result = validate_basket(db, basket, _hard("2446"))
    assert result.valid is False
    assert result.hard_budget_pass is False


def test_flexible_budget_exceeded_warns_but_does_not_hard_fail(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    intent = ShopperIntent(
        budget=BudgetIntent(amount=Decimal("2000"), type=BudgetType.FLEXIBLE),
        goal="complete_outfit",
    )
    result = validate_basket(db, basket, intent)
    assert result.valid is True
    assert result.hard_budget_pass is None
    assert "FLEXIBLE_BUDGET_EXCEEDED" in result.warnings


def test_unknown_budget_does_not_fail(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    result = validate_basket(db, basket, ShopperIntent(goal="complete_outfit"))
    assert result.valid is True
    assert result.hard_budget_pass is None
    assert result.warnings == []


def test_oos_sku_invalidates_basket(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-013-OS"])
    result = validate_basket(db, basket, ShopperIntent())
    assert result.valid is False
    assert result.inventory_pass is False
    assert result.invalid_items[0].reason == "OUT_OF_STOCK"


def test_quantity_greater_than_stock_invalidates(db: Session) -> None:
    shopping = _session(db)
    basket = add_item(db, create_basket(db, shopping), "SKU-010-M", quantity=3)
    result = validate_basket(db, basket, ShopperIntent())
    assert result.valid is False
    assert result.invalid_items[0].reason == "QUANTITY_UNAVAILABLE"


def test_version_increments_and_prior_version_reconstructable(db: Session) -> None:
    shopping = _session(db)
    v1 = add_item(db, create_basket(db, shopping), "SKU-004-M")
    assert version_label(v1) == basket_version_ref(v1.ref_id, 1)
    v2 = add_item(db, v1, "SKU-007-M")
    assert v2.version == 2
    assert version_label(v2).endswith("@v2")
    prior = get_basket(db, v1.ref_id, version=1)
    assert prior is not None
    by_label = get_basket(db, version_label(v1))
    assert by_label is not None and by_label.version == 1
    assert [item.variant.ref_id for item in prior.items] == ["SKU-004-M"]
    assert snapshot_subtotal(prior) == Decimal("1399.00")
    assert [item.variant.ref_id for item in v2.items] == ["SKU-004-M", "SKU-007-M"]


def test_approved_basket_is_not_mutated_in_place(db: Session) -> None:
    shopping = _session(db)
    v1 = add_item(db, create_basket(db, shopping), "SKU-004-M")
    v1.status = CheckoutState.APPROVED_UNVERIFIED.value
    db.flush()
    v2 = add_item(db, v1, "SKU-007-M")
    assert v2.id != v1.id
    assert v2.version == 2
    frozen = get_basket(db, v1.ref_id, version=1)
    assert frozen is not None
    assert [item.variant.ref_id for item in frozen.items] == ["SKU-004-M"]
    assert frozen.status == CheckoutState.APPROVED_UNVERIFIED.value


def test_complete_looks_real_skus_within_hard_budget(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2500")
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id)
    assert looks
    assert len(looks) <= 3
    hero = ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]
    assert any(sorted(look.skus) == sorted(hero) for look in looks)
    for look in looks:
        assert look.subtotal <= Decimal("2500")
        for sku in look.skus:
            assert get_variant_by_sku(db, sku) is not None
            assert sku.startswith("SKU-")


def test_accessory_omitted_when_it_would_exceed_budget(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2200")
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id)
    assert looks
    for look in looks:
        assert look.subtotal <= Decimal("2200")
        assert "SKU-012-OS" not in look.skus


def test_no_upsell_helper_hard_budget_violation(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    decision = evaluate_optional_add_on(db, basket, "SKU-012-OS", _hard("2000"))
    assert decision.allowed is False
    assert decision.reason == HARD_BUDGET_VIOLATION
    assert decision.recommended_action == NO_UPSELL
    assert decision.current_subtotal == Decimal("1899.00")
    assert decision.resulting_subtotal == Decimal("2398.00")


def test_replacement_within_budget_passes(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-002-M", "SKU-011-OS"])
    intent = _hard("2500")
    proposal = propose_replacement(
        db, basket, replace_sku="SKU-002-M", candidate_sku="SKU-014-M", intent=intent
    )
    assert proposal.acceptable is True
    assert proposal.resulting_subtotal == Decimal("1198.00")


def test_replacement_over_budget_fails(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), ["SKU-004-M", "SKU-007-M", "SKU-011-OS"])
    proposal = propose_replacement(
        db, basket, replace_sku="SKU-007-M", candidate_sku="SKU-010-M", intent=_hard("2500")
    )
    assert proposal.acceptable is False
    assert HARD_BUDGET_VIOLATION in proposal.reasons


def test_unknown_sku_cannot_be_added(db: Session) -> None:
    shopping = _session(db)
    basket = create_basket(db, shopping)
    with pytest.raises(BasketServiceError) as exc:
        add_item(db, basket, "SKU-999-M")
    assert exc.value.code == "sku_not_found"
