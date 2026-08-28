"""Catalogue and inventory query tests against the seeded database."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.seed_data import (
    OOS_SKUS,
    PRICE_OVERRIDE_SKU,
    all_product_ref_ids,
    all_variant_skus,
)
from app.layers.catalogue import (
    effective_price,
    get_available_quantity,
    get_product_by_ref_id,
    get_variant_by_sku,
    is_available,
    list_available_variants,
)
from app.models import Product, ProductVariant
from app.schemas.vocabulary import ProductCategory


def test_seeded_sku_exists(db: Session) -> None:
    variant = get_variant_by_sku(db, "SKU-004-M")
    assert variant is not None
    assert variant.product.ref_id == "PRD-004"
    assert variant.size == "M"


def test_nonexistent_sku_fails_safely(db: Session) -> None:
    assert get_variant_by_sku(db, "SKU-999-M") is None
    assert get_product_by_ref_id(db, "PRD-999") is None
    assert get_available_quantity(db, "SKU-999-M") == 0
    assert is_available(db, "SKU-999-M", 1) is False


def test_available_size_returned(db: Session) -> None:
    size_m = list_available_variants(db, size="M")
    skus = {item.ref_id for item in size_m}
    assert "SKU-004-M" in skus
    assert "SKU-001-M" in skus
    assert "SKU-004-S" not in skus


def test_oos_variant_excluded(db: Session) -> None:
    available = {item.ref_id for item in list_available_variants(db)}
    for sku in OOS_SKUS:
        assert sku not in available
        assert is_available(db, sku, 1) is False
        assert get_available_quantity(db, sku) == 0

    still_present = get_variant_by_sku(db, "SKU-013-OS")
    assert still_present is not None


def test_price_ceiling_filter(db: Session) -> None:
    cheap = list_available_variants(db, price_ceiling=Decimal("300"))
    skus = {item.ref_id for item in cheap}
    assert skus == {"SKU-011-OS"}
    under_budget = list_available_variants(db, price_ceiling=Decimal("2500"))
    assert all(effective_price(item) <= Decimal("2500") for item in under_budget)
    assert "SKU-004-M" in {item.ref_id for item in under_budget}


def test_catalogue_returns_only_seeded_products(db: Session) -> None:
    allowed_products = set(all_product_ref_ids())
    allowed_skus = set(all_variant_skus())
    db_products = set(db.scalars(select(Product.ref_id)).all())
    db_skus = set(db.scalars(select(ProductVariant.ref_id)).all())
    assert db_products == allowed_products
    assert db_skus == allowed_skus
    listed = list_available_variants(db, in_stock_only=False, include_inactive=True)
    assert {item.ref_id for item in listed} <= allowed_skus


def test_category_and_tag_filters(db: Session) -> None:
    dresses = list_available_variants(db, category=ProductCategory.DRESSES.value)
    assert dresses
    assert all(item.product.category == ProductCategory.DRESSES.value for item in dresses)
    farewell = list_available_variants(db, occasion_tag="farewell")
    assert "SKU-001-M" in {item.ref_id for item in farewell}
    elegant = list_available_variants(db, style_tag="elegant")
    assert elegant
    assert all("elegant" in item.product.style_tags for item in elegant)


def test_price_override_used_for_ceiling(db: Session) -> None:
    variant = get_variant_by_sku(db, PRICE_OVERRIDE_SKU)
    assert variant is not None
    assert effective_price(variant) == Decimal("1999.00")
    assert variant.product.base_price == Decimal("2199.00")


def test_hero_complete_look_skus_in_stock(db: Session) -> None:
    assert is_available(db, "SKU-004-M", 1)
    assert is_available(db, "SKU-007-M", 1)
    assert is_available(db, "SKU-011-OS", 1)
    total = (
        effective_price(get_variant_by_sku(db, "SKU-004-M"))
        + effective_price(get_variant_by_sku(db, "SKU-007-M"))
        + effective_price(get_variant_by_sku(db, "SKU-011-OS"))
    )
    assert total == Decimal("2447.00")


def test_low_stock_is_still_available(db: Session) -> None:
    assert get_available_quantity(db, "SKU-010-M") == 2
    assert is_available(db, "SKU-010-M", 2)
    assert is_available(db, "SKU-010-M", 3) is False
