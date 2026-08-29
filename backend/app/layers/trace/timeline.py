"""Chronological Agent Trace timeline. Persist-only; never fabricated."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.ref_ids import basket_version_ref
from app.layers.trace.load import aware
from app.layers.trace.mappers import action_trace, approval_covers, friction_trace
from app.models import (
    AgentAction,
    Approval,
    AuditEvent,
    Basket,
    CheckoutAttempt,
    Evidence,
    FrictionDiagnosis,
    Intent,
    Payment,
    PolicyDecision,
    RevalidationResult,
    ShoppingSession,
    WebhookEvent,
)
from app.schemas.trace import TimelineEvent, WhatWhyFix
from app.schemas.vocabulary import (
    Actor,
    ApprovalStatus,
    BoundedAction,
    CheckoutAttemptStatus,
    PaymentStatus,
    RevalidationStatus,
    SessionEventType,
    TraceEventType,
    WebhookProcessingStatus,
)

TIMELINE_RANK: dict[TraceEventType, int] = {
    TraceEventType.SESSION_STARTED: 0,
    TraceEventType.CUSTOMER_MESSAGE: 10,
    TraceEventType.INTENT_EXTRACTED: 20,
    TraceEventType.CATALOGUE_RETRIEVED: 25,
    TraceEventType.BASKET_CREATED: 30,
    TraceEventType.BASKET_UPDATED: 32,
    TraceEventType.NEW_BASKET_VERSION_CREATED: 35,
    TraceEventType.FRICTION_DIAGNOSED: 40,
    TraceEventType.ACTION_PROPOSED: 50,
    TraceEventType.NO_UPSELL: 50,
    TraceEventType.STOP: 50,
    TraceEventType.POLICY_VALIDATED: 60,
    TraceEventType.APPROVAL_REQUESTED: 70,
    TraceEventType.APPROVAL_GRANTED: 72,
    TraceEventType.APPROVAL_REJECTED: 72,
    TraceEventType.REVALIDATION_STARTED: 79,
    TraceEventType.REVALIDATION_PASSED: 80,
    TraceEventType.REVALIDATION_FAILED: 80,
    TraceEventType.REPLACEMENT_PROPOSED: 85,
    TraceEventType.CHECKOUT_CREATED: 90,
    TraceEventType.RAZORPAY_ORDER_CREATED: 91,
    TraceEventType.CLIENT_PAYMENT_REPORTED: 100,
    TraceEventType.WEBHOOK_RECEIVED: 110,
    TraceEventType.WEBHOOK_SIGNATURE_VERIFIED: 111,
    TraceEventType.PAYMENT_AUTHORIZED: 120,
    TraceEventType.PAYMENT_FAILED: 121,
    TraceEventType.PAYMENT_VERIFIED: 130,
}

_ACTOR_VALUES = {item.value for item in Actor}


def build_timeline(
    *,
    shopping: ShoppingSession,
    intents: list[Intent],
    baskets: list[Basket],
    frictions: list[FrictionDiagnosis],
    actions: list[AgentAction],
    friction_by_ref: dict[str, FrictionDiagnosis],
    policies: list[PolicyDecision],
    approvals: list[Approval],
    revals: list[RevalidationResult],
    checkouts: list[CheckoutAttempt],
    payments: list[Payment],
    webhooks: list[WebhookEvent],
    audits: list[AuditEvent],
    evidence: list[Evidence],
    action_by_friction: dict[str, AgentAction],
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = [
        _event(
            ref_id=shopping.ref_id,
            timestamp=shopping.created_at,
            type=TraceEventType.SESSION_STARTED,
            actor=Actor.SYSTEM,
            title="Session started",
            summary=f"Shopping session {shopping.ref_id} opened.",
            status=shopping.status,
        )
    ]
    events.extend(_session_events(shopping))
    events.extend(_intent_events(intents))
    events.extend(_basket_events(baskets))
    events.extend(_friction_events(frictions, action_by_friction))
    events.extend(_action_events(actions, friction_by_ref))
    events.extend(_policy_events(policies))
    events.extend(_approval_events(approvals))
    events.extend(_revalidation_events(revals))
    events.extend(_replacement_events(audits, evidence))
    events.extend(_checkout_events(checkouts))
    events.extend(_payment_events(payments))
    events.extend(_webhook_events(webhooks))
    return sort_timeline(events)


def sort_timeline(events: list[TimelineEvent]) -> list[TimelineEvent]:
    return sorted(
        events,
        key=lambda event: (
            aware(event.timestamp),
            TIMELINE_RANK.get(event.type, 500),
            event.type.value,
            event.ref_id,
        ),
    )


def _event(
    *,
    ref_id: str,
    timestamp: datetime,
    type: TraceEventType,
    actor: Actor,
    title: str,
    summary: str,
    status: str | None = None,
    evidence_ref_ids: list[str] | None = None,
    related_ref_ids: list[str] | None = None,
    details: dict[str, Any] | None = None,
    what_why_fix: WhatWhyFix | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        ref_id=ref_id,
        timestamp=aware(timestamp),
        type=type,
        actor=actor,
        title=title,
        summary=summary,
        status=status,
        evidence_ref_ids=list(evidence_ref_ids or []),
        related_ref_ids=[item for item in (related_ref_ids or []) if item],
        details=dict(details or {}),
        what_why_fix=what_why_fix,
    )


def _session_events(shopping: ShoppingSession) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in shopping.events:
        if row.event_type == SessionEventType.CUSTOMER_MESSAGE.value:
            text = str((row.payload or {}).get("text") or "")
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.created_at,
                    type=TraceEventType.CUSTOMER_MESSAGE,
                    actor=Actor.CUSTOMER,
                    title="Customer message",
                    summary=text[:240] if text else "Customer message recorded.",
                    evidence_ref_ids=list(row.evidence_ref_ids or []),
                )
            )
        elif row.event_type == SessionEventType.BASKET_UPDATED.value:
            actor = Actor(row.actor) if row.actor in _ACTOR_VALUES else Actor.SYSTEM
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.created_at,
                    type=TraceEventType.BASKET_UPDATED,
                    actor=actor,
                    title="Basket updated",
                    summary="Basket contents changed.",
                    evidence_ref_ids=list(row.evidence_ref_ids or []),
                )
            )
    return events


def _intent_events(intents: list[Intent]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in intents:
        parts = []
        if row.occasion:
            parts.append(row.occasion)
        if row.budget_type and row.budget_amount is not None:
            parts.append(f"{row.budget_type} ₹{row.budget_amount}")
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.INTENT_EXTRACTED,
                actor=Actor.SYSTEM,
                title="Intent extracted",
                summary=", ".join(parts) if parts else f"Intent {row.ref_id}",
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                details={
                    "occasion": row.occasion,
                    "budget_amount": str(row.budget_amount) if row.budget_amount is not None else None,
                    "budget_type": row.budget_type,
                    "goal": row.goal,
                },
            )
        )
    return events


def _basket_events(baskets: list[Basket]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for basket in baskets:
        event_type = (
            TraceEventType.BASKET_CREATED
            if basket.version == 1
            else TraceEventType.NEW_BASKET_VERSION_CREATED
        )
        skus = [item.variant.ref_id for item in basket.items if item.variant]
        label = basket_version_ref(basket.ref_id, basket.version)
        events.append(
            _event(
                ref_id=label,
                timestamp=basket.created_at,
                type=event_type,
                actor=Actor.SYSTEM,
                title="Basket created" if basket.version == 1 else f"Basket {label} created",
                summary=", ".join(skus) if skus else label,
                status=basket.status,
                related_ref_ids=[basket.ref_id],
                details={"skus": skus, "version": basket.version, "version_label": label},
            )
        )
    return events


def _friction_events(
    frictions: list[FrictionDiagnosis],
    action_by_friction: dict[str, AgentAction],
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in frictions:
        action = action_by_friction.get(row.ref_id)
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.FRICTION_DIAGNOSED,
                actor=Actor.SYSTEM,
                title=row.friction_type,
                summary=row.summary,
                status=row.status,
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                related_ref_ids=[action.ref_id] if action is not None else [],
                details={"friction_type": row.friction_type, "reason_codes": list(row.reason_codes or [])},
                what_why_fix=friction_trace(row, action).what_why_fix,
            )
        )
    return events


def _action_events(
    actions: list[AgentAction],
    friction_by_ref: dict[str, FrictionDiagnosis],
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in actions:
        action = BoundedAction(row.action)
        if action is BoundedAction.NO_UPSELL:
            event_type = TraceEventType.NO_UPSELL
        elif action is BoundedAction.STOP:
            event_type = TraceEventType.STOP
        else:
            event_type = TraceEventType.ACTION_PROPOSED
        friction = friction_by_ref.get(row.friction_ref_id) if row.friction_ref_id else None
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=event_type,
                actor=Actor.SYSTEM,
                title=action.value,
                summary=row.reason,
                status=row.status,
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                related_ref_ids=[row.friction_ref_id] if row.friction_ref_id else [],
                details={
                    "action": action.value,
                    "reason_codes": list(row.reason_codes or []),
                    "candidate_skus": list(row.candidate_skus or []),
                    "potential_revenue_not_pursued": (
                        str(row.potential_revenue_not_pursued)
                        if row.potential_revenue_not_pursued is not None
                        else None
                    ),
                },
                what_why_fix=action_trace(row, friction).what_why_fix,
            )
        )
    return events


def _policy_events(policies: list[PolicyDecision]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in policies:
        checks = [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "reason_code": item.get("reason_code"),
            }
            for item in (row.checks or [])
        ]
        named = [f"{item['name']} {item['status']}" for item in checks if item.get("name") and item.get("status")]
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.validated_at,
                type=TraceEventType.POLICY_VALIDATED,
                actor=Actor.SYSTEM,
                title=f"Policy {row.decision}",
                summary=f"{row.decision}" + (f" — {'; '.join(named)}" if named else ""),
                status=row.decision,
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                related_ref_ids=[row.action_ref_id] if row.action_ref_id else [],
                details={
                    "decision": row.decision,
                    "allowed": row.allowed,
                    "requires_customer_approval": row.requires_customer_approval,
                    "reason_codes": list(row.reason_codes or []),
                    "checks": checks,
                },
            )
        )
    return events


def _approval_events(approvals: list[Approval]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in approvals:
        covers = approval_covers(row)
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.APPROVAL_REQUESTED,
                actor=Actor.SYSTEM,
                title="Approval requested",
                summary=f"{row.ref_id} covers {covers}",
                status=ApprovalStatus.PENDING.value,
                related_ref_ids=[row.action_ref_id] if row.action_ref_id else [],
                details={
                    "covers": covers,
                    "basket_version": row.basket_version,
                    "action_ref_id": row.action_ref_id,
                },
            )
        )
        if row.status == ApprovalStatus.GRANTED.value:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.created_at,
                    type=TraceEventType.APPROVAL_GRANTED,
                    actor=Actor.CUSTOMER,
                    title="Approval granted",
                    summary=f"{row.ref_id} granted for {covers} only.",
                    status=row.status,
                    related_ref_ids=[row.action_ref_id] if row.action_ref_id else [],
                    details={"covers": covers, "basket_version": row.basket_version},
                )
            )
        elif row.status == ApprovalStatus.REJECTED.value:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.created_at,
                    type=TraceEventType.APPROVAL_REJECTED,
                    actor=Actor.CUSTOMER,
                    title="Approval rejected",
                    summary=f"{row.ref_id} rejected for {covers}.",
                    status=row.status,
                    details={"covers": covers},
                )
            )
    return events


def _revalidation_events(revals: list[RevalidationResult]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in revals:
        passed = row.status == RevalidationStatus.PASS.value
        reasons = ", ".join(row.failure_reasons or [])
        version_label = (
            basket_version_ref(row.basket_ref_id, row.basket_version)
            if row.basket_ref_id and row.basket_version is not None
            else None
        )
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.validated_at,
                type=TraceEventType.REVALIDATION_PASSED if passed else TraceEventType.REVALIDATION_FAILED,
                actor=Actor.SYSTEM,
                title=f"Revalidation {row.status}",
                summary=f"{row.status}: {reasons}" if reasons else row.status,
                status=row.status,
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                related_ref_ids=[row.approval_ref_id],
                details={
                    "approval_ref_id": row.approval_ref_id,
                    "version_label": version_label,
                    "failure_reasons": list(row.failure_reasons or []),
                    "changed_fields": list(row.changed_fields or []),
                    "checks": [
                        {
                            "name": item.get("name"),
                            "status": item.get("status"),
                            "reason_code": item.get("reason_code"),
                        }
                        for item in (row.checks or [])
                    ],
                },
            )
        )
    return events


def _replacement_events(audits: list[AuditEvent], evidence: list[Evidence]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in audits:
        if row.event_type != "oos_rescue_proposed":
            continue
        payload = row.payload or {}
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.REPLACEMENT_PROPOSED,
                actor=Actor.SYSTEM,
                title="Replacement proposed",
                summary=f"Replace {payload.get('failed_sku')} with {payload.get('candidate_sku')}",
                evidence_ref_ids=list(row.evidence_ref_ids or []),
                details={
                    "failed_sku": payload.get("failed_sku"),
                    "candidate_sku": payload.get("candidate_sku"),
                },
            )
        )
    if events:
        return events
    for row in evidence:
        if row.kind != "replacement_proposal":
            continue
        payload = row.payload or {}
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.REPLACEMENT_PROPOSED,
                actor=Actor.SYSTEM,
                title="Replacement proposed",
                summary=row.summary,
                evidence_ref_ids=[row.ref_id],
                details={
                    "failed_sku": payload.get("failed_sku"),
                    "candidate_sku": payload.get("candidate_sku"),
                },
            )
        )
    return events


def _checkout_events(checkouts: list[CheckoutAttempt]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in checkouts:
        label = basket_version_ref(row.basket_ref_id, row.basket_version)
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.created_at,
                type=TraceEventType.CHECKOUT_CREATED,
                actor=Actor.SYSTEM,
                title="Checkout created",
                summary=f"{row.ref_id} for {label}",
                status=row.status,
                related_ref_ids=[row.approval_ref_id, row.revalidation_ref_id or ""],
                details={
                    "amount_minor": row.amount_minor,
                    "approval_ref_id": row.approval_ref_id,
                    "revalidation_ref_id": row.revalidation_ref_id,
                },
            )
        )
        if row.provider_order_id:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.created_at,
                    type=TraceEventType.RAZORPAY_ORDER_CREATED,
                    actor=Actor.SYSTEM,
                    title="Razorpay order created",
                    summary=row.provider_order_id,
                    status=CheckoutAttemptStatus.ORDER_CREATED.value,
                    related_ref_ids=[row.ref_id],
                    details={
                        "provider_order_id": row.provider_order_id,
                        "amount_minor": row.amount_minor,
                    },
                )
            )
    return events


def _payment_events(payments: list[Payment]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in payments:
        checkout_ref = row.checkout_attempt.ref_id if row.checkout_attempt is not None else ""
        if row.reported_at is not None:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.reported_at,
                    type=TraceEventType.CLIENT_PAYMENT_REPORTED,
                    actor=Actor.CUSTOMER,
                    title="Client payment reported",
                    summary="Browser callback recorded. Not verified payment.",
                    status=PaymentStatus.REPORTED.value,
                    related_ref_ids=[checkout_ref],
                    details={
                        "client_reported": True,
                        "server_verified": row.status == PaymentStatus.VERIFIED.value,
                        "payment_status": row.status,
                    },
                )
            )
        stamp = row.updated_at or row.created_at
        if row.status == PaymentStatus.AUTHORIZED.value:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=stamp,
                    type=TraceEventType.PAYMENT_AUTHORIZED,
                    actor=Actor.SYSTEM,
                    title="Payment authorized",
                    summary="Provider authorized. Not captured/verified.",
                    status=row.status,
                )
            )
        if row.status == PaymentStatus.FAILED.value:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=stamp,
                    type=TraceEventType.PAYMENT_FAILED,
                    actor=Actor.SYSTEM,
                    title="Payment failed",
                    summary="Provider reported failure.",
                    status=row.status,
                )
            )
        if row.status == PaymentStatus.VERIFIED.value and row.verified_at is not None:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.verified_at,
                    type=TraceEventType.PAYMENT_VERIFIED,
                    actor=Actor.SYSTEM,
                    title="Payment verified",
                    summary="Independent webhook/server verification succeeded.",
                    status=row.status,
                    related_ref_ids=[checkout_ref],
                    details={
                        "client_reported": row.reported_at is not None,
                        "server_verified": True,
                        "provider_payment_id": row.provider_payment_id,
                    },
                )
            )
    return events


def _webhook_events(webhooks: list[WebhookEvent]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for row in webhooks:
        events.append(
            _event(
                ref_id=row.ref_id,
                timestamp=row.received_at,
                type=TraceEventType.WEBHOOK_RECEIVED,
                actor=Actor.SYSTEM,
                title="Webhook received",
                summary=row.event_type,
                status=row.processing_status,
                details={
                    "provider_event_id": row.provider_event_id,
                    "event_type": row.event_type,
                    "signature_valid": row.signature_valid,
                    "processing_status": row.processing_status,
                    "provider_order_id": row.provider_order_id,
                    "provider_payment_id": row.provider_payment_id,
                },
            )
        )
        if row.signature_valid:
            events.append(
                _event(
                    ref_id=row.ref_id,
                    timestamp=row.received_at,
                    type=TraceEventType.WEBHOOK_SIGNATURE_VERIFIED,
                    actor=Actor.SYSTEM,
                    title="Webhook signature verified",
                    summary="HMAC signature valid. Not payment verification by itself.",
                    status=WebhookProcessingStatus.VERIFIED_SIGNATURE.value,
                    details={"signature_valid": True, "provider_event_id": row.provider_event_id},
                )
            )
    return events
