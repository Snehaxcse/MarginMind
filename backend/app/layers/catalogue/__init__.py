"""Catalogue and inventory.

Source of SKU, price, and stock truth. Retrieval hard-filters before any
AI ranking. Unknown SKUs fail closed (None / 0 / False), they are not generated.
"""

from app.layers.catalogue.inventory import get_available_quantity, is_available
from app.layers.catalogue.service import (
    effective_price,
    get_product_by_ref_id,
    get_variant_by_sku,
    list_available_variants,
    list_products_by_category,
)

__all__ = [
    "effective_price",
    "get_available_quantity",
    "get_product_by_ref_id",
    "get_variant_by_sku",
    "is_available",
    "list_available_variants",
    "list_products_by_category",
]
