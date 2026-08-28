"""Catalogue and inventory.

Source of SKU, price, and stock truth. HARD constraints exclude candidates
before any AI ranking. SOFT signals never exclude. Unknown SKUs fail closed.
"""

from app.layers.catalogue.inventory import get_available_quantity, is_available
from app.layers.catalogue.service import (
    effective_price,
    filter_variants,
    get_product_by_ref_id,
    get_variant_by_sku,
    is_sku_eligible,
    list_available_variants,
    list_products_by_category,
    passes_hard_constraints,
)

__all__ = [
    "effective_price",
    "filter_variants",
    "get_available_quantity",
    "get_product_by_ref_id",
    "get_variant_by_sku",
    "is_available",
    "is_sku_eligible",
    "list_available_variants",
    "list_products_by_category",
    "passes_hard_constraints",
]
