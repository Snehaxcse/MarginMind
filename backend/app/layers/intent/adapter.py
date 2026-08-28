"""Map structured intent to HARD catalogue gates and SOFT ranking signals.

Per-item max_price is only a candidate ceiling. Basket-total HARD budget is
enforced by the basket layer, not by this adapter.
"""

from __future__ import annotations

from uuid import UUID

from app.schemas.catalogue import CatalogueConstraints, SoftCatalogueSignals
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import BudgetType, ConstraintKind


def intent_to_catalogue_inputs(
    intent: ShopperIntent,
    *,
    merchant_id: UUID,
) -> tuple[CatalogueConstraints, SoftCatalogueSignals]:
    max_price = None
    if intent.budget.type == BudgetType.HARD and intent.budget.amount is not None:
        max_price = intent.budget.amount

    constraints = CatalogueConstraints(
        merchant_id=merchant_id,
        max_price=max_price,
        required_size=intent.usual_size,
        excluded_materials=list(intent.excluded_materials),
        excluded_coverage=list(intent.excluded_coverage),
        excluded_product_refs=list(intent.excluded_product_refs),
        excluded_skus=list(intent.excluded_skus),
        require_in_stock=True,
        kind=ConstraintKind.HARD,
    )
    occasion_tags = [intent.occasion] if intent.occasion else []
    soft = SoftCatalogueSignals(
        preferred_colours=list(intent.colour_preferences),
        preferred_fits=list(intent.fit_preferences),
        style_tags=list(intent.style_preferences),
        occasion_tags=occasion_tags,
        kind=ConstraintKind.SOFT,
    )
    return constraints, soft
