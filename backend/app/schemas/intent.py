"""Shopper intent schemas. Unknown stays unknown; the provider is not commercial truth."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.vocabulary import BudgetType, ConstraintKind


class BudgetIntent(BaseModel):
    """Customer-stated budget. type=None means unknown, not FLEXIBLE-by-default."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None, gt=0)
    type: BudgetType | None = None
    currency: str | None = "INR"


class ShopperIntent(BaseModel):
    """Structured shopper intent. Lists default to empty, not guessed values."""

    model_config = ConfigDict(extra="forbid")

    occasion: str | None = None
    budget: BudgetIntent = Field(default_factory=BudgetIntent)
    height: str | None = None
    usual_size: str | None = None
    fit_preferences: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    colour_preferences: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_coverage: list[str] = Field(default_factory=list)
    excluded_product_refs: list[str] = Field(default_factory=list)
    excluded_skus: list[str] = Field(default_factory=list)
    goal: str | None = None

    @field_validator(
        "fit_preferences",
        "style_preferences",
        "colour_preferences",
        "excluded_materials",
        "excluded_coverage",
        "excluded_product_refs",
        "excluded_skus",
    )
    @classmethod
    def no_blank_tokens(cls, values: list[str]) -> list[str]:
        cleaned = [item.strip() for item in values]
        if any(not item for item in cleaned):
            raise ValueError("intent lists cannot contain blank values")
        return cleaned


class IntentExtractionResult(BaseModel):
    """Provider output. Must not include SKUs, prices, stock, offers, or payment state."""

    model_config = ConfigDict(extra="forbid")

    intent: ShopperIntent
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)

    def preference_kind(self, field: str) -> ConstraintKind:
        if field in {"excluded_materials", "excluded_coverage", "excluded_product_refs", "excluded_skus"}:
            return ConstraintKind.HARD
        if field == "budget":
            return (
                ConstraintKind.HARD
                if self.intent.budget.type == BudgetType.HARD
                else ConstraintKind.SOFT
            )
        return ConstraintKind.SOFT
