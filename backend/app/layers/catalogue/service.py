"""Deterministic catalogue retrieval. No AI ranking."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.models import Inventory, Product, ProductVariant


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


def _base_variant_query(*, include_inactive: bool = False) -> Select[tuple[ProductVariant]]:
    stmt = (
        select(ProductVariant)
        .join(ProductVariant.product)
        .outerjoin(ProductVariant.inventory)
        .options(joinedload(ProductVariant.product), joinedload(ProductVariant.inventory))
    )
    if not include_inactive:
        stmt = stmt.where(ProductVariant.is_active.is_(True), Product.is_active.is_(True))
    return stmt


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
    stmt = _base_variant_query(include_inactive=include_inactive)
    if category is not None:
        stmt = stmt.where(Product.category == category)
    if size is not None:
        stmt = stmt.where(ProductVariant.size == size)
    if occasion_tag is not None:
        stmt = stmt.where(Product.occasion_tags.contains([occasion_tag]))
    if style_tag is not None:
        stmt = stmt.where(Product.style_tags.contains([style_tag]))

    variants = list(session.scalars(stmt).unique().all())
    if in_stock_only:
        variants = [v for v in variants if _available_stock(v.inventory) > 0]
    if price_ceiling is not None:
        ceiling = Decimal(str(price_ceiling))
        variants = [v for v in variants if effective_price(v) <= ceiling]
    return variants


def list_products_by_category(session: Session, category: str) -> list[Product]:
    return list(
        session.scalars(
            select(Product).where(Product.category == category, Product.is_active.is_(True))
        ).all()
    )
