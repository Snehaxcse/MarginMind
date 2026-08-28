"""Proposed bounded actions. Proposal is not permission."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import ActionStatus, BoundedAction, FrictionType


class ProposedAction(BaseModel):
    """GDE output. Always PROPOSED in M6. Never authorized or executed here."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str | None = None
    session_ref_id: str
    friction_ref_id: str | None = None
    friction_type: FrictionType
    action: BoundedAction
    reason: str
    reason_codes: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(min_length=1)
    candidate_skus: list[str] = Field(default_factory=list)
    offer_ref_id: str | None = None
    confidence: Decimal = Field(ge=0, le=1)
    requires_policy_check: bool = True
    requires_customer_approval: bool = False
    potential_revenue_not_pursued: Decimal | None = None
    status: ActionStatus = ActionStatus.PROPOSED
    what: str
    why: str
    fix: str
