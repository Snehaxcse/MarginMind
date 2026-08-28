"""Inventory availability. Stock truth is never inferred by the LLM."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.layers.catalogue.service import get_variant_by_sku


def get_available_quantity(session: Session, sku: str) -> int:
    """Return on-hand minus reserved. Unknown SKUs return 0 (fail closed)."""
    variant = get_variant_by_sku(session, sku)
    if variant is None or variant.inventory is None:
        return 0
    return max(0, variant.inventory.quantity - variant.inventory.reserved_quantity)


def is_available(session: Session, sku: str, quantity: int = 1) -> bool:
    if quantity <= 0:
        return False
    variant = get_variant_by_sku(session, sku)
    if variant is None or not variant.is_active:
        return False
    if variant.product is None or not variant.product.is_active:
        return False
    return get_available_quantity(session, sku) >= quantity
