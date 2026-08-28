"""Deterministic Policy Engine. Proposal is not permission.

The Growth Decision Engine may propose. This module alone decides
PASS / BLOCK / APPROVAL_REQUIRED from database-backed commercial truth.
It never executes checkout, mutates baskets, applies offers, or trusts
GDE-supplied totals.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.engines.policy.policies import MerchantPolicySet, load_merchant_policies
from app.layers.approval import version_approval_covers
from app.layers.basket import latest_basket_for_session, version_label
from app.layers.catalogue import effective_price, get_available_quantity, get_variant_by_sku
from app.layers.evidence import record_evidence
from app.layers.intent import intent_to_catalogue_inputs, latest_intent_for_session, shopper_intent_from_row
from app.models import (
    Approval,
    Basket,
    Merchant,
    Offer,
    PolicyDecision as PolicyDecisionRow,
    ShoppingSession,
)
from app.schemas.action import ProposedAction
from app.schemas.basket import HARD_BUDGET_VIOLATION
from app.schemas.intent import ShopperIntent
from app.schemas.policy import PolicyCheckResult, PolicyDecision
from app.schemas.vocabulary import (
    ApprovalStatus,
    BoundedAction,
    BudgetType,
    CheckStatus,
    DiscountType,
    EvidenceKind,
    PolicyCheckName,
    PolicyVerdict,
)

PolicyValidationResult = PolicyDecision

HUNDRED = Decimal("100")
ZERO = Decimal("0")

DISPLAY_ONLY = {
    BoundedAction.GUIDE_CONFIDENCE,
    BoundedAction.SIMPLIFY_CHOICES,
    BoundedAction.RECOMMEND,
    BoundedAction.FIND_ALTERNATIVE,
    BoundedAction.NO_UPSELL,
    BoundedAction.STOP,
}
PURCHASE_PLAN_ACTIONS = {
    BoundedAction.BUILD_BASKET,
    BoundedAction.REBUILD_BASKET,
    BoundedAction.APPLY_AUTHORIZED_OFFER,
    BoundedAction.REQUEST_CHECKOUT,
}
BASKET_TOTAL_ACTIONS = {
    BoundedAction.BUILD_BASKET,
    BoundedAction.REBUILD_BASKET,
    BoundedAction.APPLY_AUTHORIZED_OFFER,
    BoundedAction.REQUEST_CHECKOUT,
}
MARGIN_ACTIONS = {
    BoundedAction.BUILD_BASKET,
    BoundedAction.REBUILD_BASKET,
    BoundedAction.APPLY_AUTHORIZED_OFFER,
    BoundedAction.REQUEST_CHECKOUT,
}

# CUSTOMER_APPROVAL_REQUIRED FAIL means "needed and missing", not a commercial BLOCK.
_SOFT_FAILS = {PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED}


def validate_action(
    db: Session,
    shopping: ShoppingSession,
    proposal: ProposedAction,
    *,
    intent: ShopperIntent | None = None,
    applied_offer_ref_ids: list[str] | None = None,
    persist: bool = True,
) -> PolicyDecision:
    """Validate a proposed action against live catalogue, policy, and approval state."""
    resolved_intent = intent or _intent_from_session(db, shopping)
    policies = load_merchant_policies(db, shopping.merchant_id)
    basket = latest_basket_for_session(db, shopping)
    lines = _lines_under_review(proposal, basket)
    now = datetime.now(timezone.utc)
    checks: list[PolicyCheckResult] = []
    evidence_ref_ids: list[str] = list(proposal.evidence_ref_ids)

    sku_states = _inspect_skus(db, shopping, lines, resolved_intent)
    checks.extend(_sku_checks(sku_states, lines))
    resulting_subtotal = _authoritative_subtotal(sku_states)
    checks.append(_hard_budget_check(proposal, resolved_intent, policies, resulting_subtotal, lines))
    checks.append(_margin_check(proposal, sku_states, policies, db, shopping))
    checks.extend(
        _offer_checks(
            db,
            shopping,
            proposal,
            policies,
            sku_states,
            resulting_subtotal,
            now,
            applied_offer_ref_ids or [],
        )
    )
    checks.append(_merchant_restrictions_check(sku_states, lines))
    checks.append(_approval_check(db, shopping, proposal, policies, basket))
    checks.append(_silent_change_check(db, shopping, proposal, policies, basket))

    decision, allowed, requires_customer_approval, reason_codes = _overall(proposal, checks, policies)
    result = PolicyDecision(
        action_ref_id=proposal.ref_id,
        session_ref_id=shopping.ref_id,
        allowed=allowed,
        requires_customer_approval=requires_customer_approval,
        requires_merchant_approval=False,
        decision=decision,
        reason_codes=reason_codes,
        checks=checks,
        evidence_ref_ids=evidence_ref_ids,
        validated_at=now,
        resulting_subtotal=resulting_subtotal,
    )
    if persist:
        result = _persist(db, shopping, proposal, result, basket)
    return result


def list_policy_decisions(db: Session, shopping: ShoppingSession) -> list[PolicyDecisionRow]:
    return list(
        db.scalars(
            select(PolicyDecisionRow)
            .where(PolicyDecisionRow.session_id == shopping.id)
            .order_by(PolicyDecisionRow.validated_at.asc(), PolicyDecisionRow.ref_id.asc())
        ).all()
    )


def get_policy_decision(db: Session, ref_id: str) -> PolicyDecisionRow | None:
    return db.scalar(select(PolicyDecisionRow).where(PolicyDecisionRow.ref_id == ref_id))


def _intent_from_session(db: Session, shopping: ShoppingSession) -> ShopperIntent:
    row = latest_intent_for_session(db, shopping)
    if row is None:
        return ShopperIntent()
    return shopper_intent_from_row(row)


def _lines_under_review(proposal: ProposedAction, basket: Basket | None) -> list[tuple[str, int]]:
    if proposal.action in {
        BoundedAction.BUILD_BASKET,
        BoundedAction.REBUILD_BASKET,
        BoundedAction.SIMPLIFY_CHOICES,
        BoundedAction.RECOMMEND,
        BoundedAction.FIND_ALTERNATIVE,
    }:
        return list(Counter(proposal.candidate_skus).items())
    if proposal.action in {BoundedAction.APPLY_AUTHORIZED_OFFER, BoundedAction.REQUEST_CHECKOUT}:
        if proposal.candidate_skus:
            return list(Counter(proposal.candidate_skus).items())
        if basket is None:
            return []
        counts: Counter[str] = Counter()
        for item in basket.items:
            if item.variant is not None:
                counts[item.variant.ref_id] += item.quantity
        return list(counts.items())
    return []


class _SkuState:
    def __init__(self, sku: str, quantity: int) -> None:
        self.sku = sku
        self.quantity = quantity
        self.exists = False
        self.product_active = False
        self.variant_active = False
        self.in_stock = False
        self.available_qty = 0
        self.price: Decimal | None = None
        self.margin_percent: Decimal | None = None
        self.category: str | None = None
        self.product_ref_id: str | None = None
        self.merchant_owned = False
        self.restricted = False
        self.hard_constraint_fail = False


def _inspect_skus(
    db: Session,
    shopping: ShoppingSession,
    lines: list[tuple[str, int]],
    intent: ShopperIntent,
) -> list[_SkuState]:
    merchant = db.get(Merchant, shopping.merchant_id)
    constraints, _soft = intent_to_catalogue_inputs(intent, merchant_id=shopping.merchant_id)
    states: list[_SkuState] = []
    for sku, qty in lines:
        state = _SkuState(sku, qty)
        variant = get_variant_by_sku(db, sku)
        if variant is None:
            states.append(state)
            continue
        state.exists = True
        product = variant.product
        state.product_active = bool(product is not None and product.is_active)
        state.variant_active = bool(variant.is_active)
        state.available_qty = get_available_quantity(db, sku)
        state.in_stock = state.available_qty >= qty
        state.price = effective_price(variant)
        if product is not None:
            state.margin_percent = Decimal(product.margin_percent)
            state.category = product.category
            state.product_ref_id = product.ref_id
            state.merchant_owned = product.merchant_id == shopping.merchant_id
        if sku in intent.excluded_skus or (
            product is not None and product.ref_id in intent.excluded_product_refs
        ):
            state.restricted = True
        if product is not None:
            if product.material in {item.strip() for item in intent.excluded_materials}:
                state.hard_constraint_fail = True
            if product.coverage in {item.strip() for item in intent.excluded_coverage}:
                state.hard_constraint_fail = True
        if merchant is not None and not merchant.is_active:
            state.merchant_owned = False
        if constraints.excluded_skus and sku in constraints.excluded_skus:
            state.restricted = True
        states.append(state)
    return states


def _authoritative_subtotal(states: list[_SkuState]) -> Decimal | None:
    if not states:
        return Decimal("0")
    if any(not item.exists or item.price is None for item in states):
        return None
    return sum((item.price * item.quantity for item in states), ZERO)


def _na(name: PolicyCheckName, detail: str | None = None) -> PolicyCheckResult:
    return PolicyCheckResult(name=name, status=CheckStatus.NA, detail=detail)


def _pass(name: PolicyCheckName, *, detail: str | None = None, value: str | None = None) -> PolicyCheckResult:
    return PolicyCheckResult(name=name, status=CheckStatus.PASS, detail=detail, value=value)


def _fail(
    name: PolicyCheckName,
    reason_code: str,
    *,
    detail: str | None = None,
    value: str | None = None,
) -> PolicyCheckResult:
    return PolicyCheckResult(
        name=name,
        status=CheckStatus.FAIL,
        reason_code=reason_code,
        detail=detail,
        value=value,
    )


def _sku_checks(states: list[_SkuState], lines: list[tuple[str, int]]) -> list[PolicyCheckResult]:
    if not lines:
        return [
            _na(PolicyCheckName.SKU_EXISTS, "No candidate SKUs on this action."),
            _na(PolicyCheckName.PRODUCT_ACTIVE, "No candidate SKUs on this action."),
            _na(PolicyCheckName.VARIANT_ACTIVE, "No candidate SKUs on this action."),
            _na(PolicyCheckName.INVENTORY, "No candidate SKUs on this action."),
        ]
    missing = [item.sku for item in states if not item.exists]
    if missing:
        sku_exists = _fail(
            PolicyCheckName.SKU_EXISTS,
            "SKU_NOT_FOUND",
            detail=f"Unknown SKU(s): {', '.join(missing)}",
        )
    else:
        sku_exists = _pass(PolicyCheckName.SKU_EXISTS, value=",".join(item.sku for item in states))

    known = [item for item in states if item.exists]
    if not known:
        product_active = _na(PolicyCheckName.PRODUCT_ACTIVE, "SKU does not exist.")
        variant_active = _na(PolicyCheckName.VARIANT_ACTIVE, "SKU does not exist.")
        inventory = _na(PolicyCheckName.INVENTORY, "SKU does not exist.")
    else:
        inactive_products = [item.sku for item in known if not item.product_active]
        inactive_variants = [item.sku for item in known if not item.variant_active]
        oos = [item.sku for item in known if not item.in_stock]
        product_active = (
            _fail(PolicyCheckName.PRODUCT_ACTIVE, "PRODUCT_INACTIVE", detail=", ".join(inactive_products))
            if inactive_products
            else _pass(PolicyCheckName.PRODUCT_ACTIVE)
        )
        variant_active = (
            _fail(PolicyCheckName.VARIANT_ACTIVE, "VARIANT_INACTIVE", detail=", ".join(inactive_variants))
            if inactive_variants
            else _pass(PolicyCheckName.VARIANT_ACTIVE)
        )
        if oos:
            inventory = _fail(
                PolicyCheckName.INVENTORY,
                "OUT_OF_STOCK",
                detail=", ".join(oos),
                value=str(oos[0]),
            )
        else:
            inventory = _pass(PolicyCheckName.INVENTORY)
    return [sku_exists, product_active, variant_active, inventory]


def _hard_budget_check(
    proposal: ProposedAction,
    intent: ShopperIntent,
    policies: MerchantPolicySet,
    resulting_subtotal: Decimal | None,
    lines: list[tuple[str, int]],
) -> PolicyCheckResult:
    if proposal.action not in BASKET_TOTAL_ACTIONS:
        return _na(PolicyCheckName.HARD_BUDGET, "Display/terminal action does not create a basket.")
    if not lines and proposal.action == BoundedAction.REQUEST_CHECKOUT:
        return _fail(PolicyCheckName.HARD_BUDGET, "BASKET_MISSING", detail="No basket to total.")
    if intent.budget.type != BudgetType.HARD or intent.budget.amount is None:
        return _na(PolicyCheckName.HARD_BUDGET, "Budget is not HARD.")
    if not policies.respect_hard_budget:
        # Fail closed: HARD budget is still non-negotiable.
        pass
    if resulting_subtotal is None:
        return _fail(
            PolicyCheckName.HARD_BUDGET,
            HARD_BUDGET_VIOLATION,
            detail="Cannot recompute total from authoritative catalogue prices.",
        )
    if resulting_subtotal > intent.budget.amount:
        return _fail(
            PolicyCheckName.HARD_BUDGET,
            HARD_BUDGET_VIOLATION,
            detail=f"Authoritative total {resulting_subtotal} exceeds HARD budget {intent.budget.amount}.",
            value=str(resulting_subtotal),
        )
    return _pass(
        PolicyCheckName.HARD_BUDGET,
        detail=f"{resulting_subtotal} <= {intent.budget.amount}",
        value=str(resulting_subtotal),
    )


def _unit_cost(price: Decimal, margin_percent: Decimal) -> Decimal:
    return price * (HUNDRED - margin_percent) / HUNDRED


def _margin_after_price(price: Decimal, cost: Decimal, discounted: Decimal) -> Decimal:
    if discounted <= ZERO:
        return ZERO
    return (discounted - cost) * HUNDRED / discounted


def _margin_check(
    proposal: ProposedAction,
    states: list[_SkuState],
    policies: MerchantPolicySet,
    db: Session,
    shopping: ShoppingSession,
) -> PolicyCheckResult:
    if proposal.action not in MARGIN_ACTIONS:
        return _na(PolicyCheckName.MARGIN, "No financial consequence.")
    known = [item for item in states if item.exists and item.price is not None and item.margin_percent is not None]
    if not known:
        if proposal.action == BoundedAction.APPLY_AUTHORIZED_OFFER:
            return _fail(PolicyCheckName.MARGIN, "MARGIN_FLOOR_VIOLATION", detail="No priced SKUs to margin-check.")
        return _na(PolicyCheckName.MARGIN, "No priced SKUs.")

    floor = policies.min_margin_percent
    offer = None
    if proposal.action == BoundedAction.APPLY_AUTHORIZED_OFFER and proposal.offer_ref_id:
        offer = db.scalar(
            select(Offer).where(
                Offer.ref_id == proposal.offer_ref_id,
                Offer.merchant_id == shopping.merchant_id,
            )
        )
        if offer is not None:
            floor = max(floor, Decimal(offer.min_margin_percent))

    worst: Decimal | None = None
    violators: list[str] = []
    for item in known:
        price = item.price or ZERO
        cost = _unit_cost(price, item.margin_percent or ZERO)
        discounted = price
        if offer is not None and _line_eligible(item, offer):
            discounted = _discounted_unit_price(item, offer, known)
        margin = _margin_after_price(price, cost, discounted)
        worst = margin if worst is None else min(worst, margin)
        if margin < floor:
            violators.append(f"{item.sku}:{margin:.2f}")
    if violators:
        return _fail(
            PolicyCheckName.MARGIN,
            "MARGIN_FLOOR_VIOLATION",
            detail=f"Margin floor {floor}; violators {', '.join(violators)}",
            value=str(floor),
        )
    return _pass(PolicyCheckName.MARGIN, value=str(worst), detail=f"Floor {floor}")


def _line_eligible(item: _SkuState, offer: Offer) -> bool:
    cats = list(offer.eligible_categories or [])
    refs = list(offer.eligible_product_ref_ids or [])
    if refs and item.product_ref_id in refs:
        return True
    if cats and item.category in cats:
        return True
    if not cats and not refs:
        return True
    return bool(refs and item.product_ref_id in refs) or bool(cats and item.category in cats)


def _discounted_unit_price(item: _SkuState, offer: Offer, known: list[_SkuState]) -> Decimal:
    eligible = [row for row in known if _line_eligible(row, offer)]
    eligible_total = sum((row.price * row.quantity for row in eligible), ZERO)
    if eligible_total <= ZERO or item.price is None:
        return item.price or ZERO
    raw = _raw_discount_amount(offer, eligible_total)
    cap = offer.max_discount_amount
    discount = min(raw, eligible_total)
    if cap is not None:
        discount = min(discount, Decimal(cap))
    share = (item.price * item.quantity) / eligible_total
    line_discount = discount * share
    return item.price - (line_discount / item.quantity)


def _raw_discount_amount(offer: Offer, eligible_total: Decimal) -> Decimal:
    if offer.discount_type == DiscountType.PERCENT.value:
        return eligible_total * Decimal(offer.discount_value) / HUNDRED
    return Decimal(offer.discount_value)


def _offer_checks(
    db: Session,
    shopping: ShoppingSession,
    proposal: ProposedAction,
    policies: MerchantPolicySet,
    states: list[_SkuState],
    resulting_subtotal: Decimal | None,
    now: datetime,
    applied_offer_ref_ids: list[str],
) -> list[PolicyCheckResult]:
    if proposal.action != BoundedAction.APPLY_AUTHORIZED_OFFER:
        return [
            _na(PolicyCheckName.AUTHORIZED_OFFER, "Action does not apply an offer."),
            _na(PolicyCheckName.OFFER_ACTIVE, "Action does not apply an offer."),
            _na(PolicyCheckName.OFFER_ELIGIBILITY, "Action does not apply an offer."),
            _na(PolicyCheckName.OFFER_STACKING, "Action does not apply an offer."),
        ]
    ref = proposal.offer_ref_id
    if not ref:
        fail = _fail(PolicyCheckName.AUTHORIZED_OFFER, "UNKNOWN_OFFER", detail="No offer_ref_id on proposal.")
        return [
            fail,
            _fail(PolicyCheckName.OFFER_ACTIVE, "UNKNOWN_OFFER"),
            _fail(PolicyCheckName.OFFER_ELIGIBILITY, "UNKNOWN_OFFER"),
            _na(PolicyCheckName.OFFER_STACKING, "Unknown offer."),
        ]

    offer = db.scalar(select(Offer).where(Offer.ref_id == ref))
    if offer is None:
        fail = _fail(PolicyCheckName.AUTHORIZED_OFFER, "UNKNOWN_OFFER", detail=f"{ref} is not a real offer.")
        return [
            fail,
            _fail(PolicyCheckName.OFFER_ACTIVE, "UNKNOWN_OFFER", detail=f"{ref} does not exist."),
            _fail(PolicyCheckName.OFFER_ELIGIBILITY, "UNKNOWN_OFFER"),
            _na(PolicyCheckName.OFFER_STACKING, "Unknown offer cannot be stacked."),
        ]

    authorized = (
        _pass(PolicyCheckName.AUTHORIZED_OFFER, value=offer.ref_id)
        if offer.merchant_id == shopping.merchant_id and policies.only_authorised_offers
        else _fail(
            PolicyCheckName.AUTHORIZED_OFFER,
            "OFFER_NOT_AUTHORIZED",
            detail=f"{ref} is not merchant-authorized.",
        )
    )
    if offer.merchant_id != shopping.merchant_id:
        authorized = _fail(
            PolicyCheckName.AUTHORIZED_OFFER,
            "OFFER_NOT_AUTHORIZED",
            detail=f"{ref} is not owned by this merchant.",
        )
    if not policies.only_authorised_offers and offer.merchant_id == shopping.merchant_id:
        # Fail closed: still require a real merchant offer row.
        authorized = _pass(PolicyCheckName.AUTHORIZED_OFFER, value=offer.ref_id)

    active_ok = offer.is_active and offer.starts_at <= now <= offer.ends_at
    if not offer.is_active:
        offer_active = _fail(PolicyCheckName.OFFER_ACTIVE, "OFFER_INACTIVE", value=ref)
    elif now < offer.starts_at or now > offer.ends_at:
        offer_active = _fail(PolicyCheckName.OFFER_ACTIVE, "OFFER_EXPIRED", value=ref)
    else:
        offer_active = _pass(PolicyCheckName.OFFER_ACTIVE, value=ref)
    _ = active_ok

    eligibility = _offer_eligibility(offer, states, resulting_subtotal, policies)
    stacking = _offer_stacking(offer, policies, applied_offer_ref_ids, ref)
    return [authorized, offer_active, eligibility, stacking]


def _offer_eligibility(
    offer: Offer,
    states: list[_SkuState],
    resulting_subtotal: Decimal | None,
    policies: MerchantPolicySet,
) -> PolicyCheckResult:
    known = [item for item in states if item.exists]
    if resulting_subtotal is None:
        return _fail(PolicyCheckName.OFFER_ELIGIBILITY, "OFFER_NOT_ELIGIBLE", detail="Cannot total basket.")
    if resulting_subtotal < Decimal(offer.min_basket_amount):
        return _fail(
            PolicyCheckName.OFFER_ELIGIBILITY,
            "OFFER_BASKET_MINIMUM",
            detail=f"Basket {resulting_subtotal} < min {offer.min_basket_amount}",
            value=str(resulting_subtotal),
        )
    eligible_lines = [item for item in known if _line_eligible(item, offer)]
    if not eligible_lines:
        return _fail(
            PolicyCheckName.OFFER_ELIGIBILITY,
            "OFFER_NOT_ELIGIBLE",
            detail="No eligible SKU/category in the proposed basket.",
        )
    eligible_total = sum((item.price * item.quantity for item in eligible_lines), ZERO)
    intended_pct = _intended_discount_percent(offer, eligible_total)
    if intended_pct > policies.max_discount_percent:
        return _fail(
            PolicyCheckName.OFFER_ELIGIBILITY,
            "DISCOUNT_MAX_VIOLATION",
            detail=f"Discount {intended_pct}% exceeds merchant max {policies.max_discount_percent}%",
            value=str(intended_pct),
        )
    return _pass(PolicyCheckName.OFFER_ELIGIBILITY, value=offer.ref_id)


def _intended_discount_percent(offer: Offer, eligible_total: Decimal) -> Decimal:
    if eligible_total <= ZERO:
        return HUNDRED
    if offer.discount_type == DiscountType.PERCENT.value:
        return Decimal(offer.discount_value)
    return Decimal(offer.discount_value) * HUNDRED / eligible_total


def _offer_stacking(
    offer: Offer,
    policies: MerchantPolicySet,
    applied_offer_ref_ids: list[str],
    proposed_ref: str,
) -> PolicyCheckResult:
    others = [ref for ref in applied_offer_ref_ids if ref and ref != proposed_ref]
    if not others:
        return _pass(PolicyCheckName.OFFER_STACKING, detail="No conflicting offer on the basket.")
    if not policies.offer_stacking_allowed or not offer.stackable:
        return _fail(
            PolicyCheckName.OFFER_STACKING,
            "OFFER_STACKING_PROHIBITED",
            detail=f"Cannot stack {proposed_ref} with {', '.join(others)}",
        )
    return _pass(PolicyCheckName.OFFER_STACKING)


def _merchant_restrictions_check(
    states: list[_SkuState],
    lines: list[tuple[str, int]],
) -> PolicyCheckResult:
    if not lines:
        return _na(PolicyCheckName.MERCHANT_RESTRICTIONS, "No SKUs to restrict.")
    if any(not item.exists for item in states):
        return _na(PolicyCheckName.MERCHANT_RESTRICTIONS, "Unknown SKU already failed SKU_EXISTS.")
    blocked = [
        item.sku
        for item in states
        if item.restricted or item.hard_constraint_fail or not item.merchant_owned
    ]
    if blocked:
        return _fail(
            PolicyCheckName.MERCHANT_RESTRICTIONS,
            "MERCHANT_RESTRICTION",
            detail=", ".join(blocked),
        )
    return _pass(PolicyCheckName.MERCHANT_RESTRICTIONS)


def _approval_check(
    db: Session,
    shopping: ShoppingSession,
    proposal: ProposedAction,
    policies: MerchantPolicySet,
    basket: Basket | None,
) -> PolicyCheckResult:
    if proposal.action in DISPLAY_ONLY:
        return _na(
            PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED,
            "Guidance/terminal actions do not require customer approval to show.",
        )
    if proposal.action not in PURCHASE_PLAN_ACTIONS:
        return _na(PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED)
    if proposal.action == BoundedAction.REQUEST_CHECKOUT and not policies.approval_required_before_checkout:
        return _na(PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED, "Checkout approval policy disabled.")
    if basket is not None and proposal.ref_id and version_approval_covers(
        db, shopping, basket, action_ref_id=proposal.ref_id
    ):
        return _pass(
            PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED,
            detail=f"Granted approval covers {version_label(basket)} and {proposal.ref_id}",
            value=version_label(basket),
        )
    return _fail(
        PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED,
        "CUSTOMER_APPROVAL_REQUIRED",
        detail="Purchase-plan change requires customer approval of the exact version.",
    )


def _silent_change_check(
    db: Session,
    shopping: ShoppingSession,
    proposal: ProposedAction,
    policies: MerchantPolicySet,
    basket: Basket | None,
) -> PolicyCheckResult:
    if not policies.no_silent_basket_changes:
        return _na(PolicyCheckName.NO_SILENT_BASKET_CHANGE, "Policy disabled; fail closed still forbids mutation.")
    if proposal.action in DISPLAY_ONLY:
        return _na(PolicyCheckName.NO_SILENT_BASKET_CHANGE, "Action does not mutate a basket.")
    if basket is None:
        if proposal.action == BoundedAction.REQUEST_CHECKOUT:
            return _fail(PolicyCheckName.NO_SILENT_BASKET_CHANGE, "BASKET_MISSING", detail="No basket to approve.")
        return _pass(PolicyCheckName.NO_SILENT_BASKET_CHANGE, detail="No prior approved version.")
    prior = db.scalar(
        select(Approval).where(
            Approval.session_id == shopping.id,
            Approval.status == ApprovalStatus.GRANTED.value,
            Approval.basket.has(Basket.ref_id == basket.ref_id),
        )
    )
    if prior is None:
        return _pass(PolicyCheckName.NO_SILENT_BASKET_CHANGE, detail="No granted approval to stale.")
    if prior.basket_version != basket.version or prior.basket_id != basket.id:
        return _pass(
            PolicyCheckName.NO_SILENT_BASKET_CHANGE,
            detail=(
                f"Prior approval of {basket.ref_id}@v{prior.basket_version} "
                f"does not authorize {version_label(basket)}."
            ),
            value=version_label(basket),
        )
    if proposal.action in {BoundedAction.REBUILD_BASKET, BoundedAction.BUILD_BASKET}:
        return _pass(
            PolicyCheckName.NO_SILENT_BASKET_CHANGE,
            detail="Replacement/rebuild requires a new approval; current version is not silently mutated.",
        )
    return _pass(PolicyCheckName.NO_SILENT_BASKET_CHANGE)


def _overall(
    proposal: ProposedAction,
    checks: Iterable[PolicyCheckResult],
    policies: MerchantPolicySet,
) -> tuple[PolicyVerdict, bool, bool, list[str]]:
    _ = policies
    check_list = list(checks)
    hard_fails = [
        item for item in check_list if item.status is CheckStatus.FAIL and item.name not in _SOFT_FAILS
    ]
    approval_fail = any(
        item.status is CheckStatus.FAIL and item.name is PolicyCheckName.CUSTOMER_APPROVAL_REQUIRED
        for item in check_list
    )
    reason_codes = [item.reason_code for item in check_list if item.reason_code]
    if hard_fails:
        return PolicyVerdict.BLOCK, False, False, reason_codes
    if proposal.action in PURCHASE_PLAN_ACTIONS and approval_fail:
        return PolicyVerdict.APPROVAL_REQUIRED, True, True, reason_codes
    return PolicyVerdict.PASS, True, False, reason_codes


def _persist(
    db: Session,
    shopping: ShoppingSession,
    proposal: ProposedAction,
    result: PolicyDecision,
    basket: Basket | None,
) -> PolicyDecision:
    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.POLICY_DECISION.value,
        summary=f"{proposal.action.value} → {result.decision.value}",
        payload={
            "action": proposal.action.value,
            "action_ref_id": proposal.ref_id,
            "decision": result.decision.value,
            "allowed": result.allowed,
            "requires_customer_approval": result.requires_customer_approval,
            "reason_codes": result.reason_codes,
            "checks": [item.model_dump(mode="json") for item in result.checks],
            "resulting_subtotal": (
                str(result.resulting_subtotal) if result.resulting_subtotal is not None else None
            ),
            "basket_ref": version_label(basket) if basket is not None else None,
            "offer_ref_id": proposal.offer_ref_id,
            "potential_revenue_not_pursued": (
                str(proposal.potential_revenue_not_pursued)
                if proposal.potential_revenue_not_pursued is not None
                else None
            ),
        },
    )
    refs = list(result.evidence_ref_ids)
    if evidence.ref_id not in refs:
        refs.append(evidence.ref_id)
    row = PolicyDecisionRow(
        ref_id=next_numeric_ref_id(db, PolicyDecisionRow, RefPrefix.POLICY_DECISION),
        session_id=shopping.id,
        action_ref_id=proposal.ref_id,
        decision=result.decision.value,
        allowed=result.allowed,
        requires_customer_approval=result.requires_customer_approval,
        requires_merchant_approval=False,
        reason_codes=list(result.reason_codes),
        checks=[item.model_dump(mode="json") for item in result.checks],
        evidence_ref_ids=refs,
        validated_at=result.validated_at,
    )
    db.add(row)
    db.flush()
    return result.model_copy(update={"ref_id": row.ref_id, "evidence_ref_ids": refs})
