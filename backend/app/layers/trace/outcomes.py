"""Deterministic outcome, payment stages, and guardrail derivation."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.layers.basket import snapshot_subtotal
from app.layers.catalogue import get_variant_by_sku
from app.models import (
    AgentAction,
    Approval,
    AuditEvent,
    Basket,
    CheckoutAttempt,
    Intent,
    Offer,
    Payment,
    PolicyDecision,
    RevalidationResult,
    ShoppingSession,
    WebhookEvent,
)
from app.schemas.trace import GuardrailSummary, PaymentStageSummary
from app.schemas.vocabulary import (
    ApprovalStatus,
    BoundedAction,
    BudgetType,
    CheckoutAttemptStatus,
    PaymentStatus,
    PolicyVerdict,
    RevalidationStatus,
    TraceOutcome,
    WebhookProcessingStatus,
)

_PENDING = {
    CheckoutAttemptStatus.PAYMENT_REPORTED.value,
    CheckoutAttemptStatus.VERIFICATION_PENDING.value,
    PaymentStatus.REPORTED.value,
    PaymentStatus.VERIFICATION_PENDING.value,
    PaymentStatus.AUTHORIZED.value,
}
_READY = {
    CheckoutAttemptStatus.CHECKOUT_PRESENTED.value,
    CheckoutAttemptStatus.ORDER_CREATED.value,
    CheckoutAttemptStatus.READY_FOR_PROVIDER.value,
}


def payment_stages(
    checkouts: list[CheckoutAttempt],
    payments: list[Payment],
    webhooks: list[WebhookEvent],
) -> PaymentStageSummary:
    latest_checkout = checkouts[-1] if checkouts else None
    latest_payment = payments[-1] if payments else None
    client_status = None
    server_status = None
    payment_status = None
    if latest_payment is not None:
        payment_status = latest_payment.status
        if latest_payment.reported_at is not None:
            client_status = "PAYMENT_REPORTED"
        if latest_payment.status == PaymentStatus.VERIFIED.value:
            server_status = PaymentStatus.VERIFIED.value
        elif latest_payment.status in {
            PaymentStatus.VERIFICATION_PENDING.value,
            PaymentStatus.REPORTED.value,
        }:
            server_status = PaymentStatus.VERIFICATION_PENDING.value
        elif latest_payment.status == PaymentStatus.FAILED.value:
            server_status = PaymentStatus.FAILED.value
        else:
            server_status = latest_payment.status
    if any(row.status == PaymentStatus.VERIFIED.value for row in payments):
        server_status = PaymentStatus.VERIFIED.value
        payment_status = PaymentStatus.VERIFIED.value
    signature = None
    if webhooks:
        signature = any(row.signature_valid for row in webhooks)
    return PaymentStageSummary(
        checkout_status=latest_checkout.status if latest_checkout is not None else None,
        client_status=client_status,
        server_status=server_status,
        webhook_signature_valid=signature,
        payment_status=payment_status,
    )


def final_outcome(
    *,
    actions: list[AgentAction],
    policies: list[PolicyDecision],
    approvals: list[Approval],
    revals: list[RevalidationResult],
    baskets: list[Basket],
    checkouts: list[CheckoutAttempt],
    payments: list[Payment],
    audits: list[AuditEvent],
) -> TraceOutcome:
    if any(row.status == PaymentStatus.VERIFIED.value for row in payments) or any(
        row.status == CheckoutAttemptStatus.VERIFIED.value for row in checkouts
    ):
        return TraceOutcome.PAYMENT_VERIFIED
    if any(row.status == PaymentStatus.FAILED.value for row in payments) or any(
        row.status == CheckoutAttemptStatus.FAILED.value for row in checkouts
    ):
        return TraceOutcome.PAYMENT_FAILED
    if any(row.status in _PENDING for row in checkouts) or any(row.status in _PENDING for row in payments):
        return TraceOutcome.PAYMENT_PENDING_VERIFICATION
    if any(row.status in _READY for row in checkouts):
        return TraceOutcome.CHECKOUT_READY
    if approvals and approvals[-1].status == ApprovalStatus.REJECTED.value:
        return TraceOutcome.PURCHASE_PLAN_REJECTED
    blocked = {
        row.action_ref_id for row in policies if row.decision == PolicyVerdict.BLOCK.value
    }
    if any(
        row.action == BoundedAction.STOP.value and row.ref_id not in blocked for row in actions
    ):
        return TraceOutcome.STOPPED
    if any(row.event_type == "oos_rescue_stop" for row in audits):
        return TraceOutcome.STOPPED
    failed_revals = [row for row in revals if row.status != RevalidationStatus.PASS.value]
    if failed_revals:
        latest_basket_version = max((row.version for row in baskets), default=0)
        failed_version = failed_revals[-1].basket_version or 0
        if latest_basket_version <= failed_version:
            return TraceOutcome.STOPPED
    return TraceOutcome.IN_PROGRESS


def guardrail_summary(
    db: Session,
    *,
    shopping: ShoppingSession,
    intent: Intent | None,
    baskets: list[Basket],
    approvals: list[Approval],
    checkouts: list[CheckoutAttempt],
    payments: list[Payment],
    webhooks: list[WebhookEvent],
    revals: list[RevalidationResult],
) -> GuardrailSummary:
    hard_budget = _hard_budget_amount(intent)
    known_offers = set(
        db.scalars(select(Offer.ref_id).where(Offer.merchant_id == shopping.merchant_id)).all()
    )
    basket_by_key = {(row.ref_id, row.version): row for row in baskets}
    approval_by_ref = {row.ref_id: row for row in approvals}
    reval_by_ref = {row.ref_id: row for row in revals}

    invented: set[str] = set()
    unauthorized: set[str] = set()
    executed: list[Basket] = []

    for approval in approvals:
        if approval.status != ApprovalStatus.GRANTED.value:
            continue
        basket = approval.basket
        if basket is None:
            snapshot_ref = str((approval.snapshot or {}).get("basket_ref") or "")
            basket = basket_by_key.get((snapshot_ref.split("@v")[0], approval.basket_version))
        if basket is not None:
            executed.append(basket)
        for sku in (approval.snapshot or {}).get("skus") or []:
            if get_variant_by_sku(db, sku) is None:
                invented.add(sku)
        offer_ref = (approval.snapshot or {}).get("offer_ref_id")
        if offer_ref and offer_ref not in known_offers:
            unauthorized.add(offer_ref)

    for checkout in checkouts:
        if checkout.provider_order_id:
            basket = basket_by_key.get((checkout.basket_ref_id, checkout.basket_version))
            if basket is not None:
                executed.append(basket)

    hard_budget_violation_count = 0
    if hard_budget is not None:
        counted: set[tuple[str, int]] = set()
        for basket in executed:
            key = (basket.ref_id, basket.version)
            if key in counted:
                continue
            counted.add(key)
            if snapshot_subtotal(basket) > hard_budget:
                hard_budget_violation_count += 1
        for checkout in checkouts:
            if not checkout.provider_order_id:
                continue
            amount = Decimal(checkout.amount_minor) / Decimal(100)
            key = (checkout.basket_ref_id, checkout.basket_version)
            if amount > hard_budget and key not in counted:
                hard_budget_violation_count += 1

    unapproved_money_action_count = 0
    for checkout in checkouts:
        approval = approval_by_ref.get(checkout.approval_ref_id)
        if (
            approval is None
            or approval.status != ApprovalStatus.GRANTED.value
            or approval.basket_version != checkout.basket_version
            or (approval.basket is not None and approval.basket.ref_id != checkout.basket_ref_id)
        ):
            unapproved_money_action_count += 1

    verified_by_checkout: dict[str, int] = defaultdict(int)
    for payment in payments:
        if payment.status == PaymentStatus.VERIFIED.value and payment.checkout_attempt is not None:
            verified_by_checkout[payment.checkout_attempt.ref_id] += 1
    duplicate = sum(max(0, count - 1) for count in verified_by_checkout.values())
    processed_by_checkout: dict[str, int] = defaultdict(int)
    for hook in webhooks:
        if hook.processing_status != WebhookProcessingStatus.PROCESSED.value:
            continue
        if hook.event_type not in {"payment.captured", "order.paid"}:
            continue
        if hook.checkout_attempt is not None:
            processed_by_checkout[hook.checkout_attempt.ref_id] += 1
    duplicate = max(duplicate, sum(max(0, count - 1) for count in processed_by_checkout.values()))

    incorrect_oos = 0
    for checkout in checkouts:
        if not checkout.provider_order_id:
            continue
        reval = reval_by_ref.get(checkout.revalidation_ref_id or "")
        if reval is None:
            continue
        if reval.status != RevalidationStatus.PASS.value and "OUT_OF_STOCK" in (reval.failure_reasons or []):
            incorrect_oos += 1

    return GuardrailSummary(
        hard_budget_violation_count=hard_budget_violation_count,
        invented_sku_count=len(invented),
        unauthorized_offer_count=len(unauthorized),
        unapproved_money_action_count=unapproved_money_action_count,
        duplicate_payment_effect_count=duplicate,
        incorrect_oos_checkout_count=incorrect_oos,
    )


def _hard_budget_amount(intent: Intent | None) -> Decimal | None:
    if intent is None:
        return None
    if intent.budget_type != BudgetType.HARD.value or intent.budget_amount is None:
        return None
    return Decimal(intent.budget_amount)
