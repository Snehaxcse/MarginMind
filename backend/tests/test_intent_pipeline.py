"""Intent extraction pipeline with StubLLMProvider. No Gemini. No GDE."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline import process_customer_message
from app.db.seed_data import OOS_SKUS, all_variant_skus
from app.layers.catalogue import effective_price, get_variant_by_sku
from app.layers.intent import intent_to_catalogue_inputs
from app.layers.session import create_session, list_session_events
from app.models import Evidence, Intent
from app.providers.llm.stub import (
    DINNER_UTTERANCE,
    FORCE_FAIL_UTTERANCE,
    HERO_UTTERANCE,
    LEATHER_UTTERANCE,
    NAVY_BUDGET_UTTERANCE,
    StubLLMProvider,
)
from app.schemas.intent import BudgetIntent, IntentExtractionResult, ShopperIntent
from app.schemas.vocabulary import BudgetType, ConstraintKind, SessionEventType


def _open_session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def test_hero_farewell_pipeline(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=HERO_UTTERANCE,
        provider=StubLLMProvider(),
    )
    db.flush()
    assert result.ok is True
    assert result.session_ref_id == shopping.ref_id
    assert result.intent_ref_id and result.intent_ref_id.startswith("INT-")
    assert result.evidence_ref_id and result.evidence_ref_id.startswith("EVD-")

    events = list_session_events(db, shopping)
    assert any(item.event_type == SessionEventType.CUSTOMER_MESSAGE.value for item in events)
    evidence = db.scalar(select(Evidence).where(Evidence.ref_id == result.evidence_ref_id))
    assert evidence is not None
    assert evidence.kind == "customer_message"
    assert evidence.payload["source"] == "shopper"
    assert evidence.payload["content"] == HERO_UTTERANCE

    stored = db.scalar(select(Intent).where(Intent.ref_id == result.intent_ref_id))
    assert stored is not None
    assert stored.budget_amount == Decimal("2500")
    assert stored.budget_type == BudgetType.HARD.value
    assert stored.height == "5ft2"
    assert stored.occasion == "farewell"
    assert stored.goal == "complete_outfit"
    assert "relaxed_waist" in stored.fit_preferences
    assert result.extraction is not None
    assert result.extraction.preference_kind("fit_preferences") is ConstraintKind.SOFT
    dump = result.extraction.intent.model_dump()
    assert "SKU-" not in str(dump)
    assert result.extraction.intent.excluded_skus == []

    assert result.constraints is not None
    assert result.constraints.max_price == Decimal("2500")
    assert result.soft_signals is not None
    assert "relaxed_waist" in result.soft_signals.preferred_fits
    assert "relaxed_waist" not in result.constraints.excluded_fits

    seeded = set(all_variant_skus())
    assert set(result.eligible_skus) <= seeded
    for sku in OOS_SKUS:
        assert sku not in result.eligible_skus
    for sku in result.eligible_skus:
        variant = get_variant_by_sku(db, sku)
        assert variant is not None
        assert variant.is_active and variant.product.is_active
        assert effective_price(variant) <= Decimal("2500")


def test_dinner_does_not_invent_budget_or_size(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=DINNER_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert result.ok is True
    intent = result.extraction.intent
    assert intent.occasion == "dinner"
    assert intent.budget.amount is None
    assert intent.budget.type is None
    assert intent.usual_size is None
    assert intent.excluded_skus == []
    assert "budget" in result.extraction.missing_fields
    assert result.constraints is not None
    assert result.constraints.max_price is None
    assert result.constraints.required_size is None


def test_under_2000_navy_hard_budget_soft_colour(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=NAVY_BUDGET_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert result.ok is True
    intent = result.extraction.intent
    assert intent.budget.amount == Decimal("2000")
    assert intent.budget.type is BudgetType.HARD
    assert intent.colour_preferences == ["navy"]
    assert result.constraints.max_price == Decimal("2000")
    assert result.soft_signals.preferred_colours == ["navy"]
    black = get_variant_by_sku(db, "SKU-003-M")
    assert black is not None
    assert black.ref_id in result.eligible_skus


def test_max_2500_no_leather_is_hard_exclusion(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=LEATHER_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert result.ok is True
    intent = result.extraction.intent
    assert intent.budget.type is BudgetType.HARD
    assert intent.budget.amount == Decimal("2500")
    assert intent.excluded_materials == ["leather"]
    assert result.constraints.excluded_materials == ["leather"]
    assert result.extraction.preference_kind("excluded_materials") is ConstraintKind.HARD


def test_unknown_utterance_does_not_invent_facts(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message="asdf qwerty zebra",
        provider=StubLLMProvider(),
    )
    assert result.ok is True
    intent = result.extraction.intent
    assert intent.occasion is None
    assert intent.budget.amount is None
    assert intent.usual_size is None
    assert intent.excluded_skus == []
    assert "SKU-" not in str(intent.model_dump())
    assert result.extraction.confidence <= 0.3


def test_provider_failure_does_not_persist_intent(db: Session) -> None:
    shopping = _open_session(db)
    result = process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=FORCE_FAIL_UTTERANCE,
        provider=StubLLMProvider(),
    )
    assert result.ok is False
    assert result.intent_ref_id is None
    assert result.evidence_ref_id is not None
    assert result.error_code == "stub_forced_failure"
    intents = list(db.scalars(select(Intent).where(Intent.session_id == shopping.id)))
    assert intents == []
    events = list_session_events(db, shopping)
    assert any(item.event_type == SessionEventType.PROVIDER_FAILED.value for item in events)


def test_adapter_hard_budget_is_item_ceiling_not_basket_total() -> None:
    intent = ShopperIntent(
        budget=BudgetIntent(amount=Decimal("2500"), type=BudgetType.HARD),
        fit_preferences=["relaxed_waist"],
    )
    from uuid import uuid4

    constraints, soft = intent_to_catalogue_inputs(intent, merchant_id=uuid4())
    assert constraints.max_price == Decimal("2500")
    assert "relaxed_waist" in soft.preferred_fits
    assert constraints.excluded_fits == []


def test_flexible_budget_is_not_a_hard_price_gate() -> None:
    from uuid import uuid4

    intent = ShopperIntent(
        budget=BudgetIntent(amount=Decimal("2500"), type=BudgetType.FLEXIBLE),
    )
    constraints, _soft = intent_to_catalogue_inputs(intent, merchant_id=uuid4())
    assert constraints.max_price is None


def test_extraction_result_rejects_commerce_fields() -> None:
    with pytest.raises(ValidationError):
        IntentExtractionResult.model_validate(
            {
                "intent": {},
                "confidence": 0.9,
                "candidate_skus": ["SKU-001-M"],
            }
        )
