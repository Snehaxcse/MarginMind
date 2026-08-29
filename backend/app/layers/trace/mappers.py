"""Typed section mappers from ORM rows to Agent Trace schemas."""

from __future__ import annotations

from app.core.ref_ids import basket_version_ref
from app.layers.basket import snapshot_subtotal
from app.layers.trace.load import aware
from app.models import (
    AgentAction,
    Approval,
    Basket,
    CheckoutAttempt,
    FrictionDiagnosis,
    Intent,
    Payment,
    PolicyDecision,
    RevalidationResult,
    WebhookEvent,
)
from app.schemas.policy import PolicyCheckResult
from app.schemas.revalidation import RevalidationCheckResult
from app.schemas.trace import (
    ActionTrace,
    ApprovalTrace,
    BasketLineTrace,
    BasketTrace,
    CheckoutTrace,
    FrictionTrace,
    IntentTrace,
    PaymentTrace,
    PolicyDecisionTrace,
    RevalidationTrace,
    WebhookTrace,
    WhatWhyFix,
)
from app.schemas.vocabulary import (
    ActionStatus,
    ApprovalStatus,
    BoundedAction,
    FrictionType,
    PolicyVerdict,
    RevalidationStatus,
)


def intent_trace(row: Intent) -> IntentTrace:
    return IntentTrace(
        ref_id=row.ref_id,
        occasion=row.occasion,
        budget_amount=row.budget_amount,
        budget_type=row.budget_type,
        goal=row.goal,
        usual_size=row.usual_size,
        fit_preferences=list(row.fit_preferences or []),
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        created_at=aware(row.created_at),
    )


def basket_trace(basket: Basket, *, include_margin: bool) -> BasketTrace:
    lines: list[BasketLineTrace] = []
    ordered = sorted(
        basket.items,
        key=lambda line: (line.variant.ref_id if line.variant else "", str(line.id)),
    )
    for item in ordered:
        variant = item.variant
        product = variant.product if variant is not None else None
        lines.append(
            BasketLineTrace(
                sku=variant.ref_id if variant is not None else "",
                quantity=item.quantity,
                unit_price=item.unit_price_snapshot,
                product_name=product.name if product is not None else None,
                margin_percent=(product.margin_percent if include_margin and product is not None else None),
            )
        )
    return BasketTrace(
        ref_id=basket.ref_id,
        version=basket.version,
        version_label=basket_version_ref(basket.ref_id, basket.version),
        status=basket.status,
        subtotal=snapshot_subtotal(basket),
        lines=lines,
        created_at=aware(basket.created_at),
    )


def what_why_fix(
    *,
    what: str,
    why: list[str],
    why_detail: str | None,
    fix: str | None,
    fix_detail: str | None,
) -> WhatWhyFix:
    return WhatWhyFix(
        what=what,
        why=list(why),
        why_detail=why_detail or None,
        fix=fix,
        fix_detail=fix_detail or None,
    )


def friction_trace(row: FrictionDiagnosis, action: AgentAction | None) -> FrictionTrace:
    friction_type = FrictionType(row.friction_type)
    return FrictionTrace(
        ref_id=row.ref_id,
        friction_type=friction_type,
        confidence=row.confidence,
        reason_codes=list(row.reason_codes or []),
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        summary=row.summary,
        why=row.why,
        status=row.status,
        created_at=aware(row.created_at),
        what_why_fix=what_why_fix(
            what=friction_type.value,
            why=list(row.reason_codes or []),
            why_detail=row.why,
            fix=action.action if action is not None else None,
            fix_detail=action.fix if action is not None else None,
        ),
    )


def action_trace(row: AgentAction, friction: FrictionDiagnosis | None) -> ActionTrace:
    action = BoundedAction(row.action)
    friction_type = FrictionType(friction.friction_type) if friction is not None else None
    what = friction_type.value if friction_type is not None else action.value
    why_codes = list(row.reason_codes or [])
    if not why_codes and friction is not None:
        why_codes = list(friction.reason_codes or [])
    return ActionTrace(
        ref_id=row.ref_id,
        action=action,
        status=ActionStatus(row.status),
        friction_ref_id=row.friction_ref_id,
        friction_type=friction_type,
        reason_codes=list(row.reason_codes or []),
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        candidate_skus=list(row.candidate_skus or []),
        offer_ref_id=row.offer_ref_id,
        potential_revenue_not_pursued=row.potential_revenue_not_pursued,
        created_at=aware(row.created_at),
        what_why_fix=what_why_fix(
            what=what,
            why=why_codes,
            why_detail=row.why or (friction.why if friction is not None else None),
            fix=action.value,
            fix_detail=row.fix,
        ),
    )


def policy_trace(row: PolicyDecision) -> PolicyDecisionTrace:
    checks: list[PolicyCheckResult] = []
    for item in row.checks or []:
        try:
            checks.append(PolicyCheckResult.model_validate(item))
        except Exception:
            continue
    return PolicyDecisionTrace(
        ref_id=row.ref_id,
        action_ref_id=row.action_ref_id,
        decision=PolicyVerdict(row.decision),
        allowed=row.allowed,
        requires_customer_approval=row.requires_customer_approval,
        reason_codes=list(row.reason_codes or []),
        checks=checks,
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        validated_at=aware(row.validated_at),
    )


def approval_covers(row: Approval) -> str:
    basket_ref = _approval_basket_ref(row)
    if basket_ref:
        return basket_version_ref(basket_ref, row.basket_version)
    return f"@v{row.basket_version}"


def approval_trace(row: Approval) -> ApprovalTrace:
    basket_ref = _approval_basket_ref(row)
    label = approval_covers(row)
    return ApprovalTrace(
        ref_id=row.ref_id,
        action_ref_id=row.action_ref_id,
        basket_ref_id=basket_ref,
        basket_version=row.basket_version,
        version_label=label,
        covers=label,
        status=ApprovalStatus(row.status),
        created_at=aware(row.created_at),
        decided_at=None,
    )


def revalidation_trace(row: RevalidationResult) -> RevalidationTrace:
    checks: list[RevalidationCheckResult] = []
    for item in row.checks or []:
        try:
            checks.append(RevalidationCheckResult.model_validate(item))
        except Exception:
            continue
    version_label = None
    if row.basket_ref_id is not None and row.basket_version is not None:
        version_label = basket_version_ref(row.basket_ref_id, row.basket_version)
    return RevalidationTrace(
        ref_id=row.ref_id,
        basket_ref_id=row.basket_ref_id,
        basket_version=row.basket_version,
        version_label=version_label,
        approval_ref_id=row.approval_ref_id,
        status=RevalidationStatus(row.status),
        checks=checks,
        failure_reasons=list(row.failure_reasons or []),
        changed_fields=list(row.changed_fields or []),
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        reused=None,
        validated_at=aware(row.validated_at),
    )


def checkout_trace(row: CheckoutAttempt) -> CheckoutTrace:
    return CheckoutTrace(
        ref_id=row.ref_id,
        basket_ref_id=row.basket_ref_id,
        basket_version=row.basket_version,
        version_label=basket_version_ref(row.basket_ref_id, row.basket_version),
        approval_ref_id=row.approval_ref_id,
        revalidation_ref_id=row.revalidation_ref_id,
        status=row.status,
        amount_minor=row.amount_minor,
        currency=row.currency,
        provider_order_id=row.provider_order_id,
        created_at=aware(row.created_at),
    )


def payment_trace(row: Payment) -> PaymentTrace:
    checkout_ref = row.checkout_attempt.ref_id if row.checkout_attempt is not None else ""
    return PaymentTrace(
        ref_id=row.ref_id,
        checkout_ref_id=checkout_ref,
        status=row.status,
        amount_minor=row.amount_minor,
        currency=row.currency,
        provider_order_id=row.provider_order_id,
        provider_payment_id=row.provider_payment_id,
        reported_at=aware(row.reported_at) if row.reported_at is not None else None,
        verified_at=aware(row.verified_at) if row.verified_at is not None else None,
        client_reported=row.reported_at is not None,
    )


def webhook_trace(row: WebhookEvent) -> WebhookTrace:
    return WebhookTrace(
        ref_id=row.ref_id,
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        signature_valid=row.signature_valid,
        processing_status=row.processing_status,
        provider_order_id=row.provider_order_id,
        provider_payment_id=row.provider_payment_id,
        received_at=aware(row.received_at),
        processed_at=aware(row.processed_at) if row.processed_at is not None else None,
        failure_reason=row.failure_reason,
    )


def _approval_basket_ref(row: Approval) -> str:
    if row.basket is not None:
        return row.basket.ref_id
    label = str((row.snapshot or {}).get("basket_ref") or "")
    return label.split("@v")[0]
