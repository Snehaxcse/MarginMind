"""Deterministic catalogue retrieval. HARD filters exclude; SOFT signals never do.

No AI ranking.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.models import Inventory, Merchant, Product, ProductVariant
from app.schemas.catalogue import CatalogueConstraints, SoftCatalogueSignals

ONE_SIZE = "OS"


def effective_price(variant: ProductVariant) -> Decimal:
    if variant.price_override is not None:
        return Decimal(variant.price_override)
    return Decimal(variant.product.base_price)


def get_product_by_ref_id(session: Session, ref_id: str) -> Product | None:
    return session.scalar(select(Product).where(Product.ref_id == ref_id))


def get_variant_by_sku(session: Session, sku: str) -> ProductVariant | None:
    return session.scalar(
        select(ProductVariant)
        .options(joinedload(ProductVariant.product), joinedload(ProductVariant.inventory))
        .where(ProductVariant.ref_id == sku)
    )


def _available_stock(inventory: Inventory | None) -> int:
    if inventory is None:
        return 0
    return max(0, inventory.quantity - inventory.reserved_quantity)


def _norm(value: str) -> str:
    return value.strip().casefold()


def _token_excluded(actual: str, excluded: list[str]) -> bool:
    needle = _norm(actual)
    return any(_norm(item) == needle for item in excluded)


def _size_allowed(variant_size: str, constraints: CatalogueConstraints) -> bool:
    if constraints.required_size is None:
        return True
    if variant_size == constraints.required_size:
        return True
    return constraints.allow_one_size and _norm(variant_size) == _norm(ONE_SIZE)


def passes_hard_constraints(
    variant: ProductVariant | None,
    constraints: CatalogueConstraints,
    *,
    merchant: Merchant | None = None,
) -> bool:
    """Fail closed: missing rows, inactive, OOS, and explicit exclusions are ineligible."""
    if variant is None:
        return False
    product = variant.product
    if product is None:
        return False
    if not variant.is_active or not product.is_active:
        return False
    if merchant is not None and not merchant.is_active:
        return False
    if constraints.merchant_id is not None and product.merchant_id != constraints.merchant_id:
        return False
    if constraints.categories and product.category not in constraints.categories:
        return False
    if not _size_allowed(variant.size, constraints):
        return False
    if constraints.max_price is not None and effective_price(variant) > constraints.max_price:
        return False
    if _token_excluded(product.material, constraints.excluded_materials):
        return False
    if _token_excluded(product.coverage, constraints.excluded_coverage):
        return False
    if _token_excluded(product.fit, constraints.excluded_fits):
        return False
    if _token_excluded(product.silhouette, constraints.excluded_silhouettes):
        return False
    if product.ref_id in constraints.excluded_product_refs:
        return False
    if variant.ref_id in constraints.excluded_skus:
        return False
    if constraints.require_in_stock:
        if _available_stock(variant.inventory) < constraints.min_quantity:
            return False
    return True


def is_sku_eligible(
    session: Session,
    sku: str,
    constraints: CatalogueConstraints,
) -> bool:
    """Unknown SKUs are ineligible. Soft signals are ignored."""
    variant = get_variant_by_sku(session, sku)
    merchant = None
    if constraints.merchant_id is not None:
        merchant = session.get(Merchant, constraints.merchant_id)
        if merchant is None:
            return False
    return passes_hard_constraints(variant, constraints, merchant=merchant)


def _load_merchant(session: Session, constraints: CatalogueConstraints) -> Merchant | None:
    if constraints.merchant_id is None:
        return None
    merchant = session.get(Merchant, constraints.merchant_id)
    return merchant


def _base_variant_query() -> Select[tuple[ProductVariant]]:
    return (
        select(ProductVariant)
        .join(ProductVariant.product)
        .outerjoin(ProductVariant.inventory)
        .options(joinedload(ProductVariant.product), joinedload(ProductVariant.inventory))
    )


def filter_variants(
    session: Session,
    constraints: CatalogueConstraints,
    soft: SoftCatalogueSignals | None = None,
) -> list[ProductVariant]:
    """Return SKUs that survive HARD gates. `soft` is accepted and never used to exclude."""
    _ = soft  # ranking happens later; must not filter here
    merchant = _load_merchant(session, constraints)
    if constraints.merchant_id is not None and merchant is None:
        return []
    if merchant is not None and not merchant.is_active:
        return []

    stmt = _base_variant_query()
    if constraints.merchant_id is not None:
        stmt = stmt.where(Product.merchant_id == constraints.merchant_id)
    variants = list(session.scalars(stmt).unique().all())
    return [
        variant
        for variant in variants
        if passes_hard_constraints(variant, constraints, merchant=merchant)
    ]


def list_available_variants(
    session: Session,
    *,
    category: str | None = None,
    price_ceiling: Decimal | int | str | None = None,
    size: str | None = None,
    occasion_tag: str | None = None,
    style_tag: str | None = None,
    in_stock_only: bool = True,
    include_inactive: bool = False,
) -> list[ProductVariant]:
    """Convenience wrapper. Occasion/style kwargs are SOFT and do not exclude.

    `include_inactive=True` is a debug listing only; it does not bypass
    `filter_variants` hard gates.
    """
    _ = occasion_tag, style_tag
    if include_inactive:
        stmt = _base_variant_query()
        if category is not None:
            stmt = stmt.where(Product.category == category)
        variants = list(session.scalars(stmt).unique().all())
        if size is not None:
            variants = [item for item in variants if item.size == size]
        if in_stock_only:
            variants = [item for item in variants if _available_stock(item.inventory) > 0]
        if price_ceiling is not None:
            ceiling = Decimal(str(price_ceiling))
            variants = [item for item in variants if effective_price(item) <= ceiling]
        return variants

    constraints = CatalogueConstraints(
        max_price=Decimal(str(price_ceiling)) if price_ceiling is not None else None,
        required_size=size,
        require_in_stock=in_stock_only,
        categories=[category] if category else [],
    )
    return filter_variants(session, constraints)


def list_products_by_category(session: Session, category: str) -> list[Product]:
    return list(
        session.scalars(
            select(Product).where(Product.category == category, Product.is_active.is_(True))
        ).all()
    )
