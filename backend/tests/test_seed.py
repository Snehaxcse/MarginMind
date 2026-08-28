"""Seed idempotency and demo-entity presence."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.seed import seed_all
from app.db.seed_data import all_product_ref_ids, all_variant_skus
from app.models import (
    Customer,
    CustomerPreference,
    Merchant,
    MerchantPolicy,
    Offer,
    Product,
    ProductVariant,
)
from app.schemas.vocabulary import ConstraintKind, PolicyCode


def _counts(db: Session) -> tuple[int, int, int, int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(Merchant)) or 0,
        db.scalar(select(func.count()).select_from(Customer)) or 0,
        db.scalar(select(func.count()).select_from(Product)) or 0,
        db.scalar(select(func.count()).select_from(ProductVariant)) or 0,
        db.scalar(select(func.count()).select_from(MerchantPolicy)) or 0,
        db.scalar(select(func.count()).select_from(Offer)) or 0,
    )


def test_seed_is_idempotent(db: Session) -> None:
    first = _counts(db)
    seed_all(db)
    db.commit()
    second = _counts(db)
    assert first == second
    assert first[2] == len(all_product_ref_ids())
    assert first[3] == len(all_variant_skus())
    assert 10 <= first[2] <= 15
    assert 24 <= first[3] <= 36


def test_demo_merchant_and_policies(db: Session) -> None:
    merchant = db.scalar(select(Merchant).where(Merchant.ref_id == "MER-001"))
    assert merchant is not None
    codes = set(db.scalars(select(MerchantPolicy.code)).all())
    assert PolicyCode.RESPECT_HARD_BUDGET.value in codes
    assert PolicyCode.ONLY_REAL_INVENTORY.value in codes
    assert PolicyCode.ONLY_AUTHORISED_OFFERS.value in codes
    assert PolicyCode.NO_SILENT_BASKET_CHANGES.value in codes
    assert PolicyCode.APPROVAL_REQUIRED_BEFORE_CHECKOUT.value in codes
    stacking = db.scalar(
        select(MerchantPolicy).where(
            MerchantPolicy.code == PolicyCode.OFFER_STACKING_ALLOWED.value
        )
    )
    assert stacking is not None
    assert stacking.value_bool is False
    margin = db.scalar(
        select(MerchantPolicy).where(MerchantPolicy.code == PolicyCode.MIN_MARGIN_PERCENT.value)
    )
    assert margin is not None
    assert margin.value_numeric is not None
    assert margin.value_numeric == Decimal("30.00")


def test_demo_customer_hard_vs_soft(db: Session) -> None:
    customer = db.scalar(select(Customer).where(Customer.ref_id == "CUS-001"))
    assert customer is not None
    prefs = list(
        db.scalars(
            select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
        ).all()
    )
    by_key = {(row.key, row.value): row.kind for row in prefs}
    assert by_key[("budget_amount", "2500")] == ConstraintKind.HARD.value
    assert by_key[("fit", "relaxed_waist")] == ConstraintKind.SOFT.value
    assert by_key[("style", "elegant")] == ConstraintKind.SOFT.value
    assert by_key[("style", "youthful")] == ConstraintKind.SOFT.value
    assert by_key[("occasion", "farewell")] == ConstraintKind.SOFT.value
    assert by_key[("goal", "complete_outfit")] == ConstraintKind.SOFT.value
    assert by_key[("height", "5ft2")] == ConstraintKind.SOFT.value


def test_authorised_offers_present(db: Session) -> None:
    refs = set(db.scalars(select(Offer.ref_id)).all())
    assert refs == {"OFR-001", "OFR-002", "OFR-003"}
    hesitation = db.scalar(select(Offer).where(Offer.ref_id == "OFR-002"))
    assert hesitation is not None
    assert hesitation.is_active is True
    assert hesitation.stackable is False
    assert "dresses" in hesitation.eligible_categories
