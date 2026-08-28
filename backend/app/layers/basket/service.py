"""Versioned baskets. Catalogue prices are authoritative. Total HARD budget is enforced here."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.ref_ids import (
    RefPrefix,
    basket_version_ref,
    next_numeric_ref_id,
    parse_basket_version_ref,
)
from app.layers.catalogue import (
    effective_price,
    get_variant_by_sku,
    is_available,
    passes_hard_constraints,
)
from app.layers.catalogue.inventory import get_available_quantity
from app.layers.intent.adapter import intent_to_catalogue_inputs
from app.models import Basket, BasketItem, ProductVariant, ShoppingSession
from app.schemas.basket import (
    HARD_BUDGET_VIOLATION,
    NO_UPSELL,
    AddOnEvaluation,
    BasketValidationResult,
    InvalidBasketItem,
    ReplacementProposal,
)
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import BudgetType, CheckoutState


class BasketServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


_ITEM_LOAD = (
    selectinload(Basket.items).joinedload(BasketItem.variant).joinedload(ProductVariant.product),
    joinedload(Basket.session),
    selectinload(Basket.approvals),
)



def version_label(basket: Basket) -> str:
    return basket_version_ref(basket.ref_id, basket.version)


def _basket_query():
    return select(Basket).options(*_ITEM_LOAD).execution_options(populate_existing=True)


def _reload(db: Session, basket: Basket) -> Basket:
    db.expire(basket)
    row = db.scalar(_basket_query().where(Basket.id == basket.id))
    if row is None:
        raise BasketServiceError("unknown_basket", "Basket disappeared after write.")
    return row


def get_basket(db: Session, ref: str, *, version: int | None = None) -> Basket | None:
    base, parsed = parse_basket_version_ref(ref)
    ver = version if version is not None else parsed
    stmt = _basket_query().where(Basket.ref_id == base)
    if ver is not None:
        stmt = stmt.where(Basket.version == ver)
    else:
        stmt = stmt.order_by(Basket.version.desc())
    return db.scalar(stmt)


def require_basket(db: Session, ref: str, *, version: int | None = None) -> Basket:
    row = get_basket(db, ref, version=version)
    if row is None:
        raise BasketServiceError("unknown_basket", f"Basket {ref} was not found.")
    return row


def create_basket(db: Session, shopping: ShoppingSession) -> Basket:
    row = Basket(
        ref_id=next_numeric_ref_id(db, Basket, RefPrefix.BASKET),
        session_id=shopping.id,
        version=1,
        status=CheckoutState.DRAFT_BASKET.value,
    )
    db.add(row)
    db.flush()
    return _reload(db, row)


def snapshot_subtotal(basket: Basket) -> Decimal:
    return sum((item.unit_price_snapshot * item.quantity for item in basket.items), Decimal("0"))


def live_subtotal(db: Session, basket: Basket) -> Decimal:
    total = Decimal("0")
    for item in basket.items:
        variant = get_variant_by_sku(db, item.variant.ref_id) if item.variant else None
        if variant is None:
            continue
        total += effective_price(variant) * item.quantity
    return total


def _is_locked(basket: Basket) -> bool:
    if basket.status != CheckoutState.DRAFT_BASKET.value:
        return True
    return bool(basket.approvals)


def _next_version_number(db: Session, ref_id: str) -> int:
    current = db.scalar(select(func.max(Basket.version)).where(Basket.ref_id == ref_id))
    return int(current or 0) + 1


def _add_line(
    basket: Basket,
    variant: ProductVariant,
    *,
    quantity: int,
    price: Decimal,
) -> BasketItem:
    line = BasketItem(
        basket_id=basket.id,
        variant_id=variant.id,
        quantity=quantity,
        unit_price_snapshot=price,
        variant=variant,
    )
    basket.items.append(line)
    return line


def _fork(db: Session, basket: Basket) -> Basket:
    nxt = Basket(
        ref_id=basket.ref_id,
        session_id=basket.session_id,
        version=_next_version_number(db, basket.ref_id),
        status=CheckoutState.DRAFT_BASKET.value,
    )
    db.add(nxt)
    db.flush()
    for item in list(basket.items):
        db.add(
            BasketItem(
                basket_id=nxt.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_price_snapshot=item.unit_price_snapshot,
            )
        )
    db.flush()
    return _reload(db, nxt)


def _writable(db: Session, basket: Basket) -> Basket:
    current = _reload(db, basket)
    if _is_locked(current) or current.items:
        return _fork(db, current)
    return current


def _line_price(db: Session, sku: str) -> tuple[ProductVariant, Decimal]:
    variant = get_variant_by_sku(db, sku)
    if variant is None:
        raise BasketServiceError("sku_not_found", f"SKU {sku} does not exist.")
    if not variant.is_active or not variant.product.is_active:
        raise BasketServiceError("inactive_sku", f"SKU {sku} is not active.")
    return variant, effective_price(variant)


def add_item(db: Session, basket: Basket, sku: str, *, quantity: int = 1) -> Basket:
    if quantity <= 0:
        raise BasketServiceError("invalid_quantity", "Quantity must be positive.")
    variant, price = _line_price(db, sku)
    target = _writable(db, basket)
    existing = next((item for item in target.items if item.variant_id == variant.id), None)
    if existing is not None:
        existing.quantity += quantity
        existing.unit_price_snapshot = price
    else:
        db.add(_add_line(target, variant, quantity=quantity, price=price))
    db.flush()
    return _reload(db, target)


def remove_item(db: Session, basket: Basket, sku: str) -> Basket:
    variant = get_variant_by_sku(db, sku)
    if variant is None:
        raise BasketServiceError("sku_not_found", f"SKU {sku} does not exist.")
    target = _writable(db, basket)
    remaining = [item for item in target.items if item.variant_id != variant.id]
    if len(remaining) == len(target.items):
        raise BasketServiceError("sku_not_in_basket", f"SKU {sku} is not in this basket.")
    for item in list(target.items):
        if item.variant_id == variant.id:
            target.items.remove(item)
            db.delete(item)
    db.flush()
    return _reload(db, target)


def replace_item(db: Session, basket: Basket, old_sku: str, new_sku: str, *, quantity: int = 1) -> Basket:
    old_variant = get_variant_by_sku(db, old_sku)
    if old_variant is None:
        raise BasketServiceError("sku_not_found", f"SKU {old_sku} does not exist.")
    new_variant, price = _line_price(db, new_sku)
    target = _writable(db, basket)
    if not any(item.variant_id == old_variant.id for item in target.items):
        raise BasketServiceError("sku_not_in_basket", f"SKU {old_sku} is not in this basket.")
    for item in list(target.items):
        if item.variant_id == old_variant.id:
            target.items.remove(item)
            db.delete(item)
    db.add(_add_line(target, new_variant, quantity=quantity, price=price))
    db.flush()
    return _reload(db, target)


def set_items(db: Session, basket: Basket, skus: list[str]) -> Basket:
    """Replace contents in one material change. Prices come from the catalogue only."""
    target = _writable(db, basket)
    for item in list(target.items):
        db.delete(item)
    target.items.clear()
    db.flush()
    target = _reload(db, target)
    for sku in skus:
        variant, price = _line_price(db, sku)
        db.add(_add_line(target, variant, quantity=1, price=price))
    db.flush()
    return _reload(db, target)


def validate_basket(
    db: Session,
    basket: Basket,
    intent: ShopperIntent,
) -> BasketValidationResult:
    constraints, _soft = intent_to_catalogue_inputs(intent, merchant_id=basket.session.merchant_id)
    invalid: list[InvalidBasketItem] = []
    live_total = Decimal("0")
    for item in basket.items:
        sku = item.variant.ref_id if item.variant is not None else "UNKNOWN"
        variant = get_variant_by_sku(db, sku)
        if variant is None:
            invalid.append(InvalidBasketItem(sku=sku, reason="SKU_NOT_FOUND", quantity=item.quantity))
            continue
        if not variant.is_active or not variant.product.is_active:
            invalid.append(InvalidBasketItem(sku=sku, reason="INACTIVE", quantity=item.quantity))
            continue
        if not is_available(db, sku, item.quantity):
            available = get_available_quantity(db, sku)
            reason = "OUT_OF_STOCK" if available <= 0 else "QUANTITY_UNAVAILABLE"
            invalid.append(InvalidBasketItem(sku=sku, reason=reason, quantity=item.quantity))
            continue
        if not passes_hard_constraints(variant, constraints):
            invalid.append(
                InvalidBasketItem(sku=sku, reason="HARD_CONSTRAINT_FAIL", quantity=item.quantity)
            )
            continue
        live_total += effective_price(variant) * item.quantity

    inventory_pass = not invalid
    warnings: list[str] = []
    reasons: list[str] = [row.reason for row in invalid]
    hard_budget_pass: bool | None
    budget = intent.budget
    if budget.type is None or budget.amount is None:
        hard_budget_pass = None
    elif budget.type == BudgetType.HARD:
        hard_budget_pass = live_total <= budget.amount
        if not hard_budget_pass:
            reasons.append(HARD_BUDGET_VIOLATION)
    else:
        hard_budget_pass = None
        if live_total > budget.amount:
            warnings.append("FLEXIBLE_BUDGET_EXCEEDED")

    valid = inventory_pass and (hard_budget_pass is not False)
    return BasketValidationResult(
        valid=valid,
        subtotal=live_total,
        hard_budget_pass=hard_budget_pass,
        inventory_pass=inventory_pass,
        invalid_items=invalid,
        warnings=warnings,
        reasons=reasons,
    )


def evaluate_optional_add_on(
    db: Session,
    basket: Basket,
    sku: str,
    intent: ShopperIntent,
) -> AddOnEvaluation:
    current = live_subtotal(db, basket)
    variant = get_variant_by_sku(db, sku)
    if variant is None or not is_available(db, sku, 1):
        return AddOnEvaluation(
            allowed=False,
            sku=sku,
            current_subtotal=current,
            reason="UNAVAILABLE",
        )
    price = effective_price(variant)
    resulting = current + price
    if intent.budget.type == BudgetType.HARD and intent.budget.amount is not None:
        if resulting > intent.budget.amount:
            return AddOnEvaluation(
                allowed=False,
                sku=sku,
                current_subtotal=current,
                candidate_price=price,
                resulting_subtotal=resulting,
                reason=HARD_BUDGET_VIOLATION,
                recommended_action=NO_UPSELL,
            )
    return AddOnEvaluation(
        allowed=True,
        sku=sku,
        current_subtotal=current,
        candidate_price=price,
        resulting_subtotal=resulting,
    )


def propose_replacement(
    db: Session,
    basket: Basket,
    *,
    replace_sku: str,
    candidate_sku: str,
    intent: ShopperIntent,
) -> ReplacementProposal:
    reasons: list[str] = []
    candidate = get_variant_by_sku(db, candidate_sku)
    if candidate is None:
        return ReplacementProposal(
            acceptable=False,
            replace_sku=replace_sku,
            candidate_sku=candidate_sku,
            reasons=["SKU_NOT_FOUND"],
        )
    qty = next((item.quantity for item in basket.items if item.variant.ref_id == replace_sku), 1)
    constraints, _soft = intent_to_catalogue_inputs(intent, merchant_id=basket.session.merchant_id)
    if not candidate.is_active or not candidate.product.is_active:
        reasons.append("INACTIVE")
    if not is_available(db, candidate_sku, qty):
        reasons.append("OUT_OF_STOCK" if get_available_quantity(db, candidate_sku) <= 0 else "QUANTITY_UNAVAILABLE")
    if not passes_hard_constraints(candidate, constraints):
        reasons.append("HARD_CONSTRAINT_FAIL")

    resulting = Decimal("0")
    found = False
    for item in basket.items:
        sku = item.variant.ref_id
        if sku == replace_sku:
            found = True
            resulting += effective_price(candidate) * item.quantity
        else:
            live = get_variant_by_sku(db, sku)
            if live is None:
                reasons.append("SKU_NOT_FOUND")
                continue
            resulting += effective_price(live) * item.quantity
    if not found:
        reasons.append("SKU_NOT_IN_BASKET")
    if intent.budget.type == BudgetType.HARD and intent.budget.amount is not None:
        if resulting > intent.budget.amount:
            reasons.append(HARD_BUDGET_VIOLATION)

    return ReplacementProposal(
        acceptable=not reasons,
        replace_sku=replace_sku,
        candidate_sku=candidate_sku,
        resulting_subtotal=resulting,
        reasons=reasons,
    )
