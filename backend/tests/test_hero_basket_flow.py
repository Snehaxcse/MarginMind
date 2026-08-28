"""Hero message → stub intent → catalogue → looks → basket total ≤ ₹2500."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.pipeline import process_customer_message
from app.db.seed_data import OOS_SKUS, all_variant_skus
from app.layers.basket import (
    build_complete_looks,
    create_basket,
    set_items,
    snapshot_subtotal,
    validate_basket,
)
from app.layers.catalogue import effective_price, get_variant_by_sku
from app.layers.session import create_session
from app.providers.llm.stub import HERO_UTTERANCE, StubLLMProvider
from app.schemas.vocabulary import BudgetType, ConstraintKind


def test_hero_message_to_validated_complete_look(db: Session) -> None:
    shopping = create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")
    extracted = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=HERO_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert extracted.ok is True
    intent = extracted.extraction.intent
    assert intent.budget.type is BudgetType.HARD
    assert intent.budget.amount == Decimal("2500")
    assert extracted.extraction.preference_kind("fit_preferences") is ConstraintKind.SOFT

    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id)
    assert looks
    chosen = next(
        (look for look in looks if sorted(look.skus) == ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]),
        looks[0],
    )
    assert chosen.subtotal <= Decimal("2500")

    basket = set_items(db, create_basket(db, shopping), chosen.skus)
    result = validate_basket(db, basket, intent)
    assert result.valid is True
    assert result.hard_budget_pass is True
    assert result.subtotal <= Decimal("2500")
    assert result.subtotal == snapshot_subtotal(basket)

    seeded = set(all_variant_skus())
    for sku in chosen.skus:
        assert sku in seeded
        assert sku not in OOS_SKUS
        variant = get_variant_by_sku(db, sku)
        assert variant is not None
        line = next(item for item in basket.items if item.variant.ref_id == sku)
        assert line.unit_price_snapshot == effective_price(variant)
    assert "relaxed_waist" in intent.fit_preferences
    assert "relaxed_waist" not in (extracted.constraints.excluded_fits if extracted.constraints else [])
