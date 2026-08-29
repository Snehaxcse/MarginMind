"""Customer-safe projection of a merchant Agent Trace."""

from __future__ import annotations

from app.schemas.trace import (
    ActionTrace,
    AgentTrace,
    BasketTrace,
    CustomerProgress,
    FrictionTrace,
    TimelineEvent,
)
from app.schemas.vocabulary import PolicyCheckName, TraceAudience, TraceEventType

CUSTOMER_EVENT_TYPES = {
    TraceEventType.SESSION_STARTED,
    TraceEventType.CUSTOMER_MESSAGE,
    TraceEventType.INTENT_EXTRACTED,
    TraceEventType.BASKET_CREATED,
    TraceEventType.BASKET_UPDATED,
    TraceEventType.NEW_BASKET_VERSION_CREATED,
    TraceEventType.FRICTION_DIAGNOSED,
    TraceEventType.ACTION_PROPOSED,
    TraceEventType.NO_UPSELL,
    TraceEventType.STOP,
    TraceEventType.POLICY_VALIDATED,
    TraceEventType.APPROVAL_REQUESTED,
    TraceEventType.APPROVAL_GRANTED,
    TraceEventType.APPROVAL_REJECTED,
    TraceEventType.REVALIDATION_PASSED,
    TraceEventType.REVALIDATION_FAILED,
    TraceEventType.REPLACEMENT_PROPOSED,
    TraceEventType.CHECKOUT_CREATED,
    TraceEventType.RAZORPAY_ORDER_CREATED,
    TraceEventType.CLIENT_PAYMENT_REPORTED,
    TraceEventType.WEBHOOK_RECEIVED,
    TraceEventType.WEBHOOK_SIGNATURE_VERIFIED,
    TraceEventType.PAYMENT_AUTHORIZED,
    TraceEventType.PAYMENT_VERIFIED,
    TraceEventType.PAYMENT_FAILED,
}

_SENSITIVE_DETAIL_KEYS = {
    "margin_percent",
    "margin_band",
    "min_margin_percent",
    "potential_revenue_not_pursued",
    "state_fingerprint",
    "raw_body_hash",
    "payload_meta",
    "candidate_skus",
}


def project_customer_progress(trace: AgentTrace) -> CustomerProgress:
    return CustomerProgress(
        audience=TraceAudience.CUSTOMER,
        session_ref_id=trace.session.ref_id,
        outcome=trace.outcome,
        timeline_events=[
            _sanitize_event(event)
            for event in trace.timeline_events
            if event.type in CUSTOMER_EVENT_TYPES
        ],
        current_basket=_strip_basket(trace.current_basket),
        payment_stages=trace.payment_stages,
        friction_diagnoses=[_strip_friction(item) for item in trace.friction_diagnoses],
        agent_actions=[_strip_action(item) for item in trace.agent_actions],
    )


def _sanitize_event(event: TimelineEvent) -> TimelineEvent:
    details = {key: value for key, value in event.details.items() if key not in _SENSITIVE_DETAIL_KEYS}
    if "checks" in details and isinstance(details["checks"], list):
        details["checks"] = [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "reason_code": item.get("reason_code"),
            }
            for item in details["checks"]
            if isinstance(item, dict)
            and item.get("name") not in {PolicyCheckName.MARGIN.value, "MARGIN_VALID"}
        ]
    return event.model_copy(update={"details": details})


def _strip_basket(basket: BasketTrace | None) -> BasketTrace | None:
    if basket is None:
        return None
    return basket.model_copy(
        update={"lines": [line.model_copy(update={"margin_percent": None}) for line in basket.lines]}
    )


def _strip_friction(row: FrictionTrace) -> FrictionTrace:
    return row


def _strip_action(row: ActionTrace) -> ActionTrace:
    return row.model_copy(update={"potential_revenue_not_pursued": None, "candidate_skus": []})
