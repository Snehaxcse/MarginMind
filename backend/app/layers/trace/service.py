"""Read-only Agent Trace reconstruction from persisted commercial records."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.layers.trace.load import aware, load_merchant, load_session
from app.layers.trace.mappers import (
    action_trace,
    approval_trace,
    basket_trace,
    checkout_trace,
    friction_trace,
    intent_trace,
    payment_trace,
    policy_trace,
    revalidation_trace,
    webhook_trace,
)
from app.layers.trace.outcomes import final_outcome, guardrail_summary, payment_stages
from app.layers.trace.project import project_customer_progress
from app.layers.trace.timeline import build_timeline
from app.schemas.trace import AgentTrace, SessionTraceSummary
from app.schemas.vocabulary import TraceAudience

__all__ = ["build_agent_trace", "project_customer_progress"]


def build_agent_trace(db: Session, session_ref_id: str) -> AgentTrace:
    """Rebuild a session Agent Trace from the database only.

    Safe after process restart. Does not execute actions or mutate commercial state.
    """
    shopping = load_session(db, session_ref_id)
    merchant = load_merchant(db, shopping)
    customer = shopping.customer
    intents = sorted(shopping.intents, key=lambda row: (aware(row.created_at), row.ref_id))
    baskets = sorted(shopping.baskets, key=lambda row: (row.version, row.ref_id))
    frictions = sorted(
        shopping.friction_diagnoses, key=lambda row: (aware(row.created_at), row.ref_id)
    )
    actions = sorted(shopping.agent_actions, key=lambda row: (aware(row.created_at), row.ref_id))
    policies = sorted(
        shopping.policy_decisions, key=lambda row: (aware(row.validated_at), row.ref_id)
    )
    approvals = sorted(shopping.approvals, key=lambda row: (aware(row.created_at), row.ref_id))
    revals = sorted(shopping.revalidations, key=lambda row: (aware(row.validated_at), row.ref_id))
    checkouts = sorted(
        shopping.checkout_attempts, key=lambda row: (aware(row.created_at), row.ref_id)
    )
    payments = sorted(shopping.payments, key=lambda row: (aware(row.created_at), row.ref_id))
    webhooks = sorted(
        shopping.webhook_events, key=lambda row: (aware(row.received_at), row.ref_id)
    )
    friction_by_ref = {row.ref_id: row for row in frictions}
    action_by_friction = {row.friction_ref_id: row for row in actions if row.friction_ref_id}

    timeline = build_timeline(
        shopping=shopping,
        intents=intents,
        baskets=baskets,
        frictions=frictions,
        actions=actions,
        friction_by_ref=friction_by_ref,
        policies=policies,
        approvals=approvals,
        revals=revals,
        checkouts=checkouts,
        payments=payments,
        webhooks=webhooks,
        audits=list(shopping.audit_events),
        evidence=list(shopping.evidence),
        action_by_friction=action_by_friction,
    )
    basket_traces = [basket_trace(basket, include_margin=True) for basket in baskets]
    return AgentTrace(
        audience=TraceAudience.MERCHANT,
        session=SessionTraceSummary(
            ref_id=shopping.ref_id,
            status=shopping.status,
            merchant_ref_id=merchant.ref_id,
            customer_ref_id=customer.ref_id if customer is not None else "",
            created_at=aware(shopping.created_at),
        ),
        customer_intent=intent_trace(intents[-1]) if intents else None,
        timeline_events=timeline,
        current_basket=basket_traces[-1] if basket_traces else None,
        baskets=basket_traces,
        friction_diagnoses=[
            friction_trace(row, action_by_friction.get(row.ref_id)) for row in frictions
        ],
        agent_actions=[
            action_trace(row, friction_by_ref.get(row.friction_ref_id)) for row in actions
        ],
        policy_decisions=[policy_trace(row) for row in policies],
        approvals=[approval_trace(row) for row in approvals],
        revalidations=[revalidation_trace(row) for row in revals],
        checkout_attempts=[checkout_trace(row) for row in checkouts],
        payments=[payment_trace(row) for row in payments],
        webhook_events=[webhook_trace(row) for row in webhooks],
        payment_stages=payment_stages(checkouts, payments, webhooks),
        guardrails=guardrail_summary(
            db,
            shopping=shopping,
            intent=intents[-1] if intents else None,
            baskets=baskets,
            approvals=approvals,
            checkouts=checkouts,
            payments=payments,
            webhooks=webhooks,
            revals=revals,
        ),
        outcome=final_outcome(
            actions=actions,
            policies=policies,
            approvals=approvals,
            revals=revals,
            baskets=baskets,
            checkouts=checkouts,
            payments=payments,
            audits=list(shopping.audit_events),
        ),
    )
