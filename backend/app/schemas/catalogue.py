"""Deterministic catalogue constraint schemas. No natural-language filters."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.vocabulary import ConstraintKind


def _require_non_empty_tokens(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in values:
        token = raw.strip()
        if not token:
            raise ValueError("hard-constraint lists cannot contain empty values")
        cleaned.append(token)
    return cleaned


class CatalogueConstraints(BaseModel):
    """HARD catalogue gates. Invalid or empty tokens fail validation (not permissive)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    merchant_id: UUID | None = None
    max_price: Decimal | None = Field(default=None, gt=0)
    required_size: str | None = None
    allow_one_size: bool = True
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_coverage: list[str] = Field(default_factory=list)
    excluded_fits: list[str] = Field(default_factory=list)
    excluded_silhouettes: list[str] = Field(default_factory=list)
    excluded_product_refs: list[str] = Field(default_factory=list)
    excluded_skus: list[str] = Field(default_factory=list)
    require_in_stock: bool = True
    min_quantity: int = Field(default=1, ge=1)
    categories: list[str] = Field(default_factory=list)
    kind: ConstraintKind = ConstraintKind.HARD

    @field_validator("required_size")
    @classmethod
    def required_size_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            raise ValueError("required_size cannot be blank")
        return token

    @field_validator(
        "excluded_materials",
        "excluded_coverage",
        "excluded_fits",
        "excluded_silhouettes",
        "excluded_product_refs",
        "excluded_skus",
        "categories",
    )
    @classmethod
    def no_blank_tokens(cls, values: list[str]) -> list[str]:
        return _require_non_empty_tokens(values)

    @model_validator(mode="after")
    def kind_is_hard(self) -> CatalogueConstraints:
        if self.kind is not ConstraintKind.HARD:
            raise ValueError("CatalogueConstraints are HARD and cannot be marked SOFT")
        return self


class SoftCatalogueSignals(BaseModel):
    """SOFT ranking hints. The catalogue service must not use these to exclude SKUs."""

    model_config = ConfigDict(extra="forbid")

    preferred_colours: list[str] = Field(default_factory=list)
    preferred_silhouettes: list[str] = Field(default_factory=list)
    preferred_fits: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    occasion_tags: list[str] = Field(default_factory=list)
    kind: ConstraintKind = ConstraintKind.SOFT
