"""Persist validated intent rows. Does not invent commercial facts."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.models import Intent, ShoppingSession
from app.schemas.intent import BudgetIntent, IntentExtractionResult, ShopperIntent
from app.schemas.vocabulary import BudgetType


def persist_intent(
    db: Session,
    *,
    session: ShoppingSession,
    extraction: IntentExtractionResult,
) -> Intent:
    intent = extraction.intent
    row = Intent(
        ref_id=next_numeric_ref_id(db, Intent, RefPrefix.INTENT),
        session_id=session.id,
        customer_id=session.customer_id,
        occasion=intent.occasion,
        budget_amount=intent.budget.amount,
        budget_type=None if intent.budget.type is None else intent.budget.type.value,
        height=intent.height,
        usual_size=intent.usual_size,
        fit_preferences=list(intent.fit_preferences),
        style_preferences=list(intent.style_preferences),
        colour_preferences=list(intent.colour_preferences),
        excluded_materials=list(intent.excluded_materials),
        excluded_coverage=list(intent.excluded_coverage),
        goal=intent.goal,
        confidence=Decimal(str(extraction.confidence)),
        evidence_ref_ids=list(extraction.evidence_ref_ids),
        missing_fields=list(extraction.missing_fields),
        ambiguities=list(extraction.ambiguities),
        raw_payload=extraction.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def shopper_intent_from_row(row: Intent) -> ShopperIntent:
    budget_type = BudgetType(row.budget_type) if row.budget_type else None
    return ShopperIntent(
        occasion=row.occasion,
        budget=BudgetIntent(amount=row.budget_amount, type=budget_type),
        height=row.height,
        usual_size=row.usual_size,
        fit_preferences=list(row.fit_preferences or []),
        style_preferences=list(row.style_preferences or []),
        colour_preferences=list(row.colour_preferences or []),
        excluded_materials=list(row.excluded_materials or []),
        excluded_coverage=list(row.excluded_coverage or []),
        goal=row.goal,
    )


def latest_intent_for_session(db: Session, shopping: ShoppingSession) -> Intent | None:
    return db.scalar(
        select(Intent)
        .where(Intent.session_id == shopping.id)
        .order_by(Intent.created_at.desc(), Intent.ref_id.desc())
    )
