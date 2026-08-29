"""Agent Trace response schemas. Reconstruction only — no commercial writes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.policy import PolicyCheckResult
from app.schemas.revalidation import RevalidationCheckResult
from app.schemas.vocabulary import (
    ActionStatus,
    Actor,
    ApprovalStatus,
    BoundedAction,
    FrictionType,
    PolicyVerdict,
    RevalidationStatus,
    TraceAudience,
    TraceEventType,
    TraceOutcome,
)


class WhatWhyFix(BaseModel):
    """Structured Growth Decision fields. Not a single prose blob."""

    model_config = ConfigDict(extra="forbid")

    what: str
    why: list[str] = Field(default_factory=list)
    why_detail: str | None = None
    fix: str | None = None
    fix_detail: str | None = None


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    timestamp: datetime
    type: TraceEventType
    actor: Actor
    title: str
    summary: str
    status: str | None = None
    evidence_ref_ids: list[str] = Field(default_factory=list)
    related_ref_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    what_why_fix: WhatWhyFix | None = None


class SessionTraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    status: str
    merchant_ref_id: str
    customer_ref_id: str
    created_at: datetime


class IntentTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    occasion: str | None = None
    budget_amount: Decimal | None = None
    budget_type: str | None = None
    goal: str | None = None
    usual_size: str | None = None
    fit_preferences: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class BasketLineTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    quantity: int
    unit_price: Decimal
    product_name: str | None = None
    margin_percent: Decimal | None = None


class BasketTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    version: int
    version_label: str
    status: str
    subtotal: Decimal
    lines: list[BasketLineTrace] = Field(default_factory=list)
    created_at: datetime


class FrictionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    friction_type: FrictionType
    confidence: Decimal
    reason_codes: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    summary: str
    why: str
    status: str
    created_at: datetime
    what_why_fix: WhatWhyFix


class ActionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    action: BoundedAction
    status: ActionStatus
    friction_ref_id: str | None = None
    friction_type: FrictionType | None = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    candidate_skus: list[str] = Field(default_factory=list)
    offer_ref_id: str | None = None
    potential_revenue_not_pursued: Decimal | None = None
    created_at: datetime
    what_why_fix: WhatWhyFix


class PolicyDecisionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    action_ref_id: str | None = None
    decision: PolicyVerdict
    allowed: bool
    requires_customer_approval: bool
    reason_codes: list[str] = Field(default_factory=list)
    checks: list[PolicyCheckResult] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    validated_at: datetime


class ApprovalTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    action_ref_id: str | None = None
    basket_ref_id: str
    basket_version: int
    version_label: str
    covers: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None


class RevalidationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    basket_ref_id: str | None = None
    basket_version: int | None = None
    version_label: str | None = None
    approval_ref_id: str
    status: RevalidationStatus
    checks: list[RevalidationCheckResult] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    changed_fields: list[str] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)
    reused: bool | None = None
    validated_at: datetime


class CheckoutTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    basket_ref_id: str
    basket_version: int
    version_label: str
    approval_ref_id: str
    revalidation_ref_id: str | None = None
    status: str
    amount_minor: int
    currency: str
    provider_order_id: str | None = None
    created_at: datetime


class PaymentTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    checkout_ref_id: str
    status: str
    amount_minor: int
    currency: str
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    reported_at: datetime | None = None
    verified_at: datetime | None = None
    client_reported: bool = False


class WebhookTrace(BaseModel):
    """Safe webhook metadata. No secret, no raw body."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    provider_event_id: str | None = None
    event_type: str
    signature_valid: bool
    processing_status: str
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    received_at: datetime
    processed_at: datetime | None = None
    failure_reason: str | None = None


class PaymentStageSummary(BaseModel):
    """Keep checkout / client / server / webhook / payment distinct."""

    model_config = ConfigDict(extra="forbid")

    checkout_status: str | None = None
    client_status: str | None = None
    server_status: str | None = None
    webhook_signature_valid: bool | None = None
    payment_status: str | None = None


class GuardrailSummary(BaseModel):
    """Derived from persisted commercial truth. Never hard-coded zeros."""

    model_config = ConfigDict(extra="forbid")

    hard_budget_violation_count: int
    invented_sku_count: int
    unauthorized_offer_count: int
    unapproved_money_action_count: int
    duplicate_payment_effect_count: int
    incorrect_oos_checkout_count: int


class AgentTrace(BaseModel):
    """Merchant-complete session reconstruction."""

    model_config = ConfigDict(extra="forbid")

    audience: TraceAudience = TraceAudience.MERCHANT
    session: SessionTraceSummary
    customer_intent: IntentTrace | None = None
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    current_basket: BasketTrace | None = None
    baskets: list[BasketTrace] = Field(default_factory=list)
    friction_diagnoses: list[FrictionTrace] = Field(default_factory=list)
    agent_actions: list[ActionTrace] = Field(default_factory=list)
    policy_decisions: list[PolicyDecisionTrace] = Field(default_factory=list)
    approvals: list[ApprovalTrace] = Field(default_factory=list)
    revalidations: list[RevalidationTrace] = Field(default_factory=list)
    checkout_attempts: list[CheckoutTrace] = Field(default_factory=list)
    payments: list[PaymentTrace] = Field(default_factory=list)
    webhook_events: list[WebhookTrace] = Field(default_factory=list)
    payment_stages: PaymentStageSummary
    guardrails: GuardrailSummary
    outcome: TraceOutcome


class CustomerProgress(BaseModel):
    """Customer-safe projection. No margin, guardrails, or forgone-revenue internals."""

    model_config = ConfigDict(extra="forbid")

    audience: TraceAudience = TraceAudience.CUSTOMER
    session_ref_id: str
    outcome: TraceOutcome
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    current_basket: BasketTrace | None = None
    payment_stages: PaymentStageSummary
    friction_diagnoses: list[FrictionTrace] = Field(default_factory=list)
    agent_actions: list[ActionTrace] = Field(default_factory=list)
