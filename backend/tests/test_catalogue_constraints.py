"""HARD catalogue constraints vs SOFT signals. No AI ranking."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.layers.catalogue import (
    effective_price,
    filter_variants,
    is_sku_eligible,
    list_available_variants,
)
from app.models import Merchant, Product, ProductVariant
from app.schemas.catalogue import CatalogueConstraints, SoftCatalogueSignals


def _merchant_id(db: Session):
    merchant = db.scalar(select(Merchant).where(Merchant.ref_id == "MER-001"))
    assert merchant is not None
    return merchant.id


def _skus(variants: list[ProductVariant]) -> set[str]:
    return {item.ref_id for item in variants}


def test_hard_price_ceiling_excludes_expensive(db: Session) -> None:
    constraints = CatalogueConstraints(max_price=Decimal("300"), require_in_stock=True)
    assert _skus(filter_variants(db, constraints)) == {"SKU-011-OS"}
    assert is_sku_eligible(db, "SKU-004-M", constraints) is False
    assert is_sku_eligible(db, "SKU-011-OS", constraints) is True


def test_required_size_excludes_other_sizes(db: Session) -> None:
    constraints = CatalogueConstraints(required_size="M", require_in_stock=True)
    skus = _skus(filter_variants(db, constraints))
    assert "SKU-004-M" in skus
    assert "SKU-001-M" in skus
    assert "SKU-004-S" not in skus
    assert "SKU-004-L" not in skus
    assert "SKU-011-OS" in skus


def test_oos_variants_excluded(db: Session) -> None:
    constraints = CatalogueConstraints(require_in_stock=True)
    skus = _skus(filter_variants(db, constraints))
    assert "SKU-004-S" not in skus
    assert "SKU-005-28" not in skus
    assert "SKU-013-OS" not in skus
    assert is_sku_eligible(db, "SKU-013-OS", constraints) is False


def test_inactive_product_excluded(db: Session) -> None:
    product = db.scalar(select(Product).where(Product.ref_id == "PRD-003"))
    assert product is not None
    product.is_active = False
    db.flush()
    constraints = CatalogueConstraints(require_in_stock=True)
    assert "SKU-003-M" not in _skus(filter_variants(db, constraints))
    assert is_sku_eligible(db, "SKU-003-M", constraints) is False


def test_excluded_material_rejected(db: Session) -> None:
    constraints = CatalogueConstraints(excluded_materials=["denim"], require_in_stock=True)
    skus = _skus(filter_variants(db, constraints))
    assert "SKU-005-M" not in skus
    assert "SKU-006-M" not in skus
    assert "SKU-010-M" not in skus
    assert "SKU-004-M" in skus


def test_excluded_coverage_rejected(db: Session) -> None:
    constraints = CatalogueConstraints(excluded_coverage=["low"], require_in_stock=True)
    skus = _skus(filter_variants(db, constraints))
    assert "SKU-003-M" not in skus
    assert "SKU-001-M" in skus


def test_restricted_sku_rejected(db: Session) -> None:
    constraints = CatalogueConstraints(excluded_skus=["SKU-010-M"], require_in_stock=True)
    skus = _skus(filter_variants(db, constraints))
    assert "SKU-010-M" not in skus
    assert "SKU-004-M" in skus
    assert is_sku_eligible(db, "SKU-010-M", constraints) is False


def test_restricted_product_rejected(db: Session) -> None:
    constraints = CatalogueConstraints(excluded_product_refs=["PRD-003"], require_in_stock=True)
    assert "SKU-003-M" not in _skus(filter_variants(db, constraints))


def test_soft_colour_does_not_exclude(db: Session) -> None:
    constraints = CatalogueConstraints(max_price=Decimal("2500"), require_in_stock=True)
    soft = SoftCatalogueSignals(preferred_colours=["burgundy", "navy"])
    skus = _skus(filter_variants(db, constraints, soft=soft))
    assert "SKU-003-M" in skus
    variant = db.scalar(select(ProductVariant).where(ProductVariant.ref_id == "SKU-003-M"))
    assert variant is not None
    assert variant.colour == "black"


def test_soft_style_does_not_become_hard_filter(db: Session) -> None:
    constraints = CatalogueConstraints(required_size="M", require_in_stock=True)
    without_soft = _skus(filter_variants(db, constraints))
    with_soft = _skus(
        filter_variants(
            db,
            constraints,
            soft=SoftCatalogueSignals(style_tags=["elegant"], occasion_tags=["farewell"]),
        )
    )
    assert without_soft == with_soft
    assert "SKU-003-M" in with_soft
    listed = list_available_variants(db, size="M", style_tag="elegant", occasion_tag="farewell")
    assert _skus(listed) == with_soft


def test_unknown_sku_fails_closed(db: Session) -> None:
    constraints = CatalogueConstraints(require_in_stock=True)
    assert is_sku_eligible(db, "SKU-999-M", constraints) is False
    assert is_sku_eligible(db, "", constraints) is False


def test_unknown_merchant_fails_closed(db: Session) -> None:
    constraints = CatalogueConstraints(merchant_id=uuid4(), require_in_stock=True)
    assert filter_variants(db, constraints) == []
    assert is_sku_eligible(db, "SKU-004-M", constraints) is False


def test_malformed_hard_constraints_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CatalogueConstraints(max_price=Decimal("0"))
    with pytest.raises(ValidationError):
        CatalogueConstraints(required_size="   ")
    with pytest.raises(ValidationError):
        CatalogueConstraints(excluded_skus=["SKU-001-M", ""])
    with pytest.raises(ValidationError):
        CatalogueConstraints(excluded_materials=["denim", " "])


def test_combined_constraints_only_valid_seeded_skus(db: Session) -> None:
    constraints = CatalogueConstraints(
        merchant_id=_merchant_id(db),
        max_price=Decimal("2500"),
        required_size="M",
        excluded_materials=["denim"],
        excluded_coverage=["low"],
        excluded_skus=["SKU-008-M"],
        require_in_stock=True,
    )
    variants = filter_variants(db, constraints)
    skus = _skus(variants)
    assert skus == {
        "SKU-001-M",
        "SKU-002-M",
        "SKU-004-M",
        "SKU-007-M",
        "SKU-009-M",
        "SKU-011-OS",
        "SKU-012-OS",
        "SKU-014-M",
    }
    assert "SKU-003-M" not in skus
    assert "SKU-005-M" not in skus
    assert "SKU-010-M" not in skus
    assert "SKU-008-M" not in skus
    assert "SKU-004-S" not in skus
    assert "SKU-013-OS" not in skus
    for variant in variants:
        assert variant.is_active
        assert variant.product.is_active
        assert effective_price(variant) <= Decimal("2500")
        assert variant.size in {"M", "OS"}


def test_hero_hard_budget_size_soft_style(db: Session) -> None:
    constraints = CatalogueConstraints(
        merchant_id=_merchant_id(db),
        max_price=Decimal("2500"),
        required_size="M",
        require_in_stock=True,
    )
    soft = SoftCatalogueSignals(
        preferred_fits=["relaxed_waist"],
        style_tags=["elegant", "youthful"],
        occasion_tags=["farewell"],
    )
    variants = filter_variants(db, constraints, soft=soft)
    skus = _skus(variants)
    assert {"SKU-004-M", "SKU-007-M", "SKU-011-OS", "SKU-001-M"} <= skus
    assert "SKU-004-S" not in skus
    assert "SKU-013-OS" not in skus
    assert "SKU-003-M" in skus
    for variant in variants:
        assert variant.product.ref_id.startswith("PRD-")
        assert variant.is_active and variant.product.is_active
        assert effective_price(variant) <= Decimal("2500")
        assert variant.size in {"M", "OS"}
        assert variant.inventory is not None
        assert (variant.inventory.quantity - variant.inventory.reserved_quantity) > 0
