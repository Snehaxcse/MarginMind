"""Typed policy decisions. Proposal is not permission."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import CheckStatus, PolicyCheckName, PolicyVerdict


class PolicyCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PolicyCheckName
    status: CheckStatus
    reason_code: str | None = None
    detail: str | None = None
    value: str | None = None


class PolicyDecision(BaseModel):
    """Policy Engine output. Never executes the action."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str | None = None
    action_ref_id: str | None = None
    session_ref_id: str
    allowed: bool
    requires_customer_approval: bool = False
    requires_merchant_approval: bool = False
    decision: PolicyVerdict
    reason_codes: list[str] = Field(default_factory=list)
    checks: list[PolicyCheckResult] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    validated_at: datetime
    resulting_subtotal: Decimal | None = None


PolicyValidationResult = PolicyDecision
