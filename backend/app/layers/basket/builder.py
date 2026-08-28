"""Deterministic complete-look builder. Soft metadata scores; hard budget is a hard cap.

Scoring (integers, no AI):
- +3 per matching occasion tag
- +2 if relaxed_waist / relaxed fit matches a preferred fit
- +2 per matching style tag
- +1 per matching colour
- +4 if the look has main + supporting apparel (trousers+top) or a dress
- +5 if an accessory is included without exceeding HARD total budget
- +1 per 500 INR of remaining HARD budget (prefer complete looks that still fit)
- +3 if two or more apparel pieces share the same size (OS accessories ignored)
- +1 per size-M apparel piece when usual_size is unknown (soft default, not a filter)

HARD total budget: a look is discarded if sum(effective prices) > budget.
FLEXIBLE/unknown budget: no total cap in the builder.
Accessory is omitted when it would exceed a HARD total.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.layers.catalogue import effective_price, filter_variants
from app.layers.intent.adapter import intent_to_catalogue_inputs
from app.models import ProductVariant
from app.schemas.basket import LookCandidate
from app.schemas.catalogue import SoftCatalogueSignals
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import BudgetType, ProductCategory

_MAIN = {ProductCategory.DRESSES.value, ProductCategory.TROUSERS.value}
_SUPPORT = {ProductCategory.TOPS.value}
_ACCESSORY = {ProductCategory.ACCESSORIES.value}


def _hard_total_cap(intent: ShopperIntent) -> Decimal | None:
    if intent.budget.type == BudgetType.HARD and intent.budget.amount is not None:
        return intent.budget.amount
    return None


def _fits(total: Decimal, cap: Decimal | None) -> bool:
    return cap is None or total <= cap


def _score_variant(variant: ProductVariant, soft: SoftCatalogueSignals) -> int:
    product = variant.product
    score = 0
    for tag in soft.occasion_tags:
        if tag in product.occasion_tags:
            score += 3
    if any(pref in {"relaxed_waist", "relaxed"} for pref in soft.preferred_fits):
        if product.fit == "relaxed" or "relaxed_waist" in product.style_tags:
            score += 2
    for tag in soft.style_tags:
        if tag in product.style_tags:
            score += 2
    for colour in soft.preferred_colours:
        if colour.casefold() in {product.colour.casefold(), variant.colour.casefold()}:
            score += 1
    return score


def _apparel_sizes(variants: list[ProductVariant]) -> list[str]:
    sizes: list[str] = []
    for item in variants:
        if item.product.category in _ACCESSORY or item.size == "OS":
            continue
        sizes.append(item.size)
    return sizes


def _look_score(
    variants: list[ProductVariant],
    soft: SoftCatalogueSignals,
    *,
    cap: Decimal | None,
    subtotal: Decimal,
    usual_size: str | None,
) -> int:
    score = sum(_score_variant(item, soft) for item in variants)
    categories = {item.product.category for item in variants}
    if ProductCategory.DRESSES.value in categories:
        score += 4
    if ProductCategory.TROUSERS.value in categories and ProductCategory.TOPS.value in categories:
        score += 4
    if ProductCategory.ACCESSORIES.value in categories:
        score += 5
    apparel_sizes = _apparel_sizes(variants)
    if len(apparel_sizes) >= 2 and len(set(apparel_sizes)) == 1:
        score += 3
    if usual_size is None:
        score += sum(1 for size in apparel_sizes if size == "M")
    if cap is not None:
        remaining = cap - subtotal
        if remaining >= 0:
            score += int(remaining // 500)
    return score


def _sorted_group(variants: list[ProductVariant], soft: SoftCatalogueSignals) -> list[ProductVariant]:
    return sorted(
        variants,
        key=lambda item: (-_score_variant(item, soft), item.ref_id),
    )


def build_complete_looks(
    db: Session,
    intent: ShopperIntent,
    *,
    merchant_id: UUID,
    limit: int = 3,
) -> list[LookCandidate]:
    if intent.goal not in {None, "complete_outfit"}:
        return []
    constraints, soft = intent_to_catalogue_inputs(intent, merchant_id=merchant_id)
    eligible = filter_variants(db, constraints, soft=soft)
    cap = _hard_total_cap(intent)

    dresses = _sorted_group(
        [item for item in eligible if item.product.category == ProductCategory.DRESSES.value],
        soft,
    )
    trousers = _sorted_group(
        [item for item in eligible if item.product.category == ProductCategory.TROUSERS.value],
        soft,
    )
    tops = _sorted_group(
        [item for item in eligible if item.product.category == ProductCategory.TOPS.value],
        soft,
    )
    accessories = _sorted_group(
        [item for item in eligible if item.product.category == ProductCategory.ACCESSORIES.value],
        soft,
    )

    raw: list[list[ProductVariant]] = []

    for trouser in trousers[:6]:
        t_price = effective_price(trouser)
        for top in tops[:6]:
            if top.product_id == trouser.product_id:
                continue
            pair_total = t_price + effective_price(top)
            if not _fits(pair_total, cap):
                continue
            combo = [trouser, top]
            raw.append(combo)
            for accessory in accessories[:6]:
                with_acc = pair_total + effective_price(accessory)
                if _fits(with_acc, cap):
                    raw.append(combo + [accessory])
                    break

    for dress in dresses[:6]:
        d_price = effective_price(dress)
        if not _fits(d_price, cap):
            continue
        combo = [dress]
        raw.append(combo)
        for accessory in accessories[:6]:
            with_acc = d_price + effective_price(accessory)
            if _fits(with_acc, cap):
                raw.append(combo + [accessory])
                break

    unique: dict[tuple[str, ...], list[ProductVariant]] = {}
    for combo in raw:
        key = tuple(sorted(item.ref_id for item in combo))
        unique.setdefault(key, combo)

    ranked: list[LookCandidate] = []
    for combo in unique.values():
        subtotal = sum((effective_price(item) for item in combo), Decimal("0"))
        if not _fits(subtotal, cap):
            continue
        roles: dict[str, str] = {}
        for item in combo:
            if item.product.category in _MAIN and "main" not in roles:
                roles["main"] = item.ref_id
            elif item.product.category in _SUPPORT:
                roles["support"] = item.ref_id
            elif item.product.category in _ACCESSORY:
                roles["accessory"] = item.ref_id
        ranked.append(
            LookCandidate(
                skus=[item.ref_id for item in combo],
                subtotal=subtotal,
                score=_look_score(
                    combo, soft, cap=cap, subtotal=subtotal, usual_size=intent.usual_size
                ),
                roles=roles,
            )
        )

    ranked.sort(key=lambda look: (-look.score, -len(look.skus), look.skus))
    return ranked[:limit]
