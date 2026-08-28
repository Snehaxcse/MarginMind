"""Basket validation and look-builder result types."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import BoundedAction


class InvalidBasketItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    reason: str
    quantity: int = 1


class BasketValidationResult(BaseModel):
    """Basket-layer checks only. Not the merchant Policy Engine."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    subtotal: Decimal
    hard_budget_pass: bool | None = None
    inventory_pass: bool
    invalid_items: list[InvalidBasketItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AddOnEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    sku: str
    current_subtotal: Decimal
    candidate_price: Decimal | None = None
    resulting_subtotal: Decimal | None = None
    reason: str | None = None
    recommended_action: str | None = None


class ReplacementProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptable: bool
    replace_sku: str
    candidate_sku: str
    resulting_subtotal: Decimal | None = None
    reasons: list[str] = Field(default_factory=list)


class LookCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skus: list[str]
    subtotal: Decimal
    score: int
    roles: dict[str, str] = Field(default_factory=dict)


NO_UPSELL = BoundedAction.NO_UPSELL.value
HARD_BUDGET_VIOLATION = "HARD_BUDGET_VIOLATION"
