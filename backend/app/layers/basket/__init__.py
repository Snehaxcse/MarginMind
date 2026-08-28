"""Versioned baskets and customer approvals.

Approval binds to an exact basket version. APPROVED is not SUCCESS.
Never silently substitute lines on an approved snapshot.
Total HARD budget is enforced on the basket sum, not only per-item price.
"""

from app.layers.basket.builder import build_complete_looks
from app.layers.basket.service import (
    BasketServiceError,
    add_item,
    create_basket,
    evaluate_optional_add_on,
    get_basket,
    latest_basket_for_session,
    live_subtotal,
    propose_replacement,
    remove_item,
    replace_item,
    require_basket,
    set_items,
    snapshot_subtotal,
    validate_basket,
    version_label,
)

__all__ = [
    "BasketServiceError",
    "add_item",
    "build_complete_looks",
    "create_basket",
    "evaluate_optional_add_on",
    "get_basket",
    "latest_basket_for_session",
    "live_subtotal",
    "propose_replacement",
    "remove_item",
    "replace_item",
    "require_basket",
    "set_items",
    "snapshot_subtotal",
    "validate_basket",
    "version_label",
]
