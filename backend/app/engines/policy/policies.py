"""Typed merchant policy snapshot. Description text is never the source of truth."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MerchantPolicy
from app.schemas.vocabulary import PolicyCode


@dataclass(frozen=True)
class MerchantPolicySet:
    respect_hard_budget: bool
    only_real_inventory: bool
    only_authorised_offers: bool
    no_silent_basket_changes: bool
    approval_required_before_checkout: bool
    min_margin_percent: Decimal
    max_discount_percent: Decimal
    offer_stacking_allowed: bool


def _bool(row: MerchantPolicy | None, default: bool) -> bool:
    if row is None or row.value_bool is None:
        return default
    return bool(row.value_bool)


def _numeric(row: MerchantPolicy | None, default: Decimal) -> Decimal:
    if row is None or row.value_numeric is None:
        return default
    return Decimal(row.value_numeric)


def load_merchant_policies(db: Session, merchant_id) -> MerchantPolicySet:
    rows = list(
        db.scalars(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant_id)).all()
    )
    by_code = {row.code: row for row in rows}
    return MerchantPolicySet(
        respect_hard_budget=_bool(by_code.get(PolicyCode.RESPECT_HARD_BUDGET.value), True),
        only_real_inventory=_bool(by_code.get(PolicyCode.ONLY_REAL_INVENTORY.value), True),
        only_authorised_offers=_bool(by_code.get(PolicyCode.ONLY_AUTHORISED_OFFERS.value), True),
        no_silent_basket_changes=_bool(
            by_code.get(PolicyCode.NO_SILENT_BASKET_CHANGES.value), True
        ),
        approval_required_before_checkout=_bool(
            by_code.get(PolicyCode.APPROVAL_REQUIRED_BEFORE_CHECKOUT.value), True
        ),
        min_margin_percent=_numeric(
            by_code.get(PolicyCode.MIN_MARGIN_PERCENT.value), Decimal("30.00")
        ),
        max_discount_percent=_numeric(
            by_code.get(PolicyCode.MAX_DISCOUNT_PERCENT.value), Decimal("10.00")
        ),
        offer_stacking_allowed=_bool(
            by_code.get(PolicyCode.OFFER_STACKING_ALLOWED.value), False
        ),
    )
