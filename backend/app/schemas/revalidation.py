"""Final revalidation. Approval is not success."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import CheckStatus, RevalidationCheckName, RevalidationStatus


class RevalidationCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RevalidationCheckName
    status: CheckStatus
    reason_code: str | None = None
    detail: str | None = None
    value: str | None = None


class RevalidationResult(BaseModel):
    """Live commercial re-check of an exact approved basket version.

    Never executes checkout or mutates the approved snapshot.
    """

    model_config = ConfigDict(extra="forbid")

    ref_id: str | None = None
    session_ref_id: str
    basket_ref_id: str | None = None
    basket_version: int | None = None
    approval_ref_id: str
    status: RevalidationStatus
    checks: list[RevalidationCheckResult] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    changed_fields: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    validated_at: datetime
    offer_ref_id: str | None = None
    resulting_subtotal: Decimal | None = None
    reused: bool = False


class RescueProposal(BaseModel):
    """OOS replacement candidate. Proposal only — does not mutate the approved basket."""

    model_config = ConfigDict(extra="forbid")

    failed_sku: str
    candidate_sku: str
    reason: str
    original_basket_ref: str
    original_basket_version: int
    projected_total: Decimal
    hard_budget_pass: bool
    inventory_pass: bool
    policy_pass: bool
    policy_decision_ref: str | None = None
    requires_customer_approval: bool = True
    projected_skus: list[str] = Field(default_factory=list)


class RescueDecision(BaseModel):
    """Customer choice on a replacement proposal. Never auto-approves."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    stopped: bool = False
    reason: str | None = None
    original_basket_ref: str
    original_basket_version: int
    new_basket_ref: str | None = None
    new_basket_version: int | None = None
    approval_ref_id: str | None = None
    policy_decision_ref: str | None = None
    requires_customer_approval: bool = False
