"""OOS rescue: propose a real replacement, then optionally fork a new basket version.

Never mutates the approved snapshot unless the customer accepts.
Never auto-approves. Never invents SKUs. Never executes checkout.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.engines.policy import load_merchant_policies, validate_action
from app.layers.approval import create_approval_request
from app.layers.basket import get_basket, replace_item, version_label
from app.layers.catalogue import (
    effective_price,
    filter_variants,
    get_variant_by_sku,
    is_available,
)
from app.layers.evidence import record_audit, record_evidence
from app.layers.intent.adapter import intent_to_catalogue_inputs
from app.models import Basket, ShoppingSession
from app.schemas.action import ProposedAction
from app.schemas.intent import ShopperIntent
from app.schemas.revalidation import RescueDecision, RescueProposal, RevalidationResult
from app.schemas.vocabulary import (
    Actor,
    BoundedAction,
    BudgetType,
    EvidenceKind,
    FrictionType,
    PolicyVerdict,
    RevalidationStatus,
)


def propose_oos_replacement(
    db: Session,
    shopping: ShoppingSession,
    revalidation: RevalidationResult,
    intent: ShopperIntent,
) -> RescueProposal | None:
    """Search for one real in-stock replacement. Does not mutate any basket."""
    if revalidation.status is RevalidationStatus.PASS:
        return None
    if "OUT_OF_STOCK" not in revalidation.failure_reasons:
        return None
    if revalidation.basket_ref_id is None or revalidation.basket_version is None:
        return None
    original = get_basket(db, revalidation.basket_ref_id, version=revalidation.basket_version)
    if original is None:
        return None
    failed_sku = _failed_sku(revalidation)
    if failed_sku is None:
        return None
    failed = get_variant_by_sku(db, failed_sku)
    if failed is None or failed.product is None:
        return None

    constraints, soft = intent_to_catalogue_inputs(intent, merchant_id=shopping.merchant_id)
    constraints = constraints.model_copy(update={"categories": [failed.product.category]})
    occupied = {item.variant.ref_id for item in original.items if item.variant}
    policies = load_merchant_policies(db, shopping.merchant_id)
    ranked: list[tuple[int, str]] = []
    for variant in filter_variants(db, constraints, soft):
        if variant.ref_id == failed_sku or variant.ref_id in occupied:
            continue
        if not is_available(db, variant.ref_id, 1):
            continue
        if Decimal(variant.product.margin_percent) < policies.min_margin_percent:
            continue
        projected = _projected_skus(original, failed_sku, variant.ref_id)
        total = _projected_total(db, projected)
        if intent.budget.type is BudgetType.HARD and intent.budget.amount is not None:
            if total > intent.budget.amount:
                continue
        score = _score(variant, failed, soft.preferred_fits, soft.style_tags, soft.occasion_tags)
        ranked.append((score, variant.ref_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        record_evidence(
            db,
            session=shopping,
            kind=EvidenceKind.REPLACEMENT_PROPOSAL.value,
            summary=f"No valid replacement for {failed_sku}",
            payload={"failed_sku": failed_sku, "basket_ref": version_label(original)},
        )
        record_audit(
            db,
            session=shopping,
            actor=Actor.SYSTEM.value,
            event_type="oos_rescue_stop",
            decision="STOPPED",
            payload={"failed_sku": failed_sku},
        )
        return None

    candidate_sku = ranked[0][1]
    projected = _projected_skus(original, failed_sku, candidate_sku)
    total = _projected_total(db, projected)
    policy = validate_action(
        db,
        shopping,
        ProposedAction(
            session_ref_id=shopping.ref_id,
            friction_type=FrictionType.OUT_OF_STOCK,
            action=BoundedAction.REBUILD_BASKET,
            reason="OOS rescue replacement candidate.",
            evidence_ref_ids=list(revalidation.evidence_ref_ids or ["EVD-REVAL"]),
            candidate_skus=projected,
            confidence=Decimal("0.92"),
            what="Replace OOS SKU",
            why="Approved item is no longer in stock",
            fix="Show a real in-stock alternative. Require new approval.",
        ),
        intent=intent,
        persist=True,
    )
    if policy.decision is PolicyVerdict.BLOCK:
        return None
    inventory_ok = is_available(db, candidate_sku, 1)
    budget_ok = True
    if intent.budget.type is BudgetType.HARD and intent.budget.amount is not None:
        budget_ok = total <= intent.budget.amount
    proposal = RescueProposal(
        failed_sku=failed_sku,
        candidate_sku=candidate_sku,
        reason="FIND_ALTERNATIVE",
        original_basket_ref=original.ref_id,
        original_basket_version=original.version,
        projected_total=total,
        hard_budget_pass=budget_ok,
        inventory_pass=inventory_ok,
        policy_pass=policy.decision is not PolicyVerdict.BLOCK,
        policy_decision_ref=policy.ref_id,
        requires_customer_approval=True,
        projected_skus=projected,
    )
    record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.REPLACEMENT_PROPOSAL.value,
        summary=f"Replace {failed_sku} with {candidate_sku}",
        payload=proposal.model_dump(mode="json"),
    )
    record_audit(
        db,
        session=shopping,
        actor=Actor.SYSTEM.value,
        event_type="oos_rescue_proposed",
        decision="FIND_ALTERNATIVE",
        payload={"failed_sku": failed_sku, "candidate_sku": candidate_sku},
    )
    return proposal


def accept_oos_replacement(
    db: Session,
    shopping: ShoppingSession,
    proposal: RescueProposal,
    intent: ShopperIntent,
    *,
    action_ref_id: str | None = None,
) -> RescueDecision:
    """Customer chose the candidate. Fork a new basket version. Do not inherit v1 approval."""
    original = get_basket(db, proposal.original_basket_ref, version=proposal.original_basket_version)
    if original is None:
        return RescueDecision(
            accepted=False,
            stopped=True,
            reason="BASKET_MISSING",
            original_basket_ref=proposal.original_basket_ref,
            original_basket_version=proposal.original_basket_version,
        )
    if not is_available(db, proposal.candidate_sku, 1):
        return RescueDecision(
            accepted=False,
            stopped=True,
            reason="OUT_OF_STOCK",
            original_basket_ref=original.ref_id,
            original_basket_version=original.version,
        )
    projected_total = _projected_total(db, proposal.projected_skus)
    if intent.budget.type is BudgetType.HARD and intent.budget.amount is not None:
        if projected_total > intent.budget.amount:
            return RescueDecision(
                accepted=False,
                stopped=True,
                reason="HARD_BUDGET_VIOLATION",
                original_basket_ref=original.ref_id,
                original_basket_version=original.version,
            )
    policy = validate_action(
        db,
        shopping,
        ProposedAction(
            session_ref_id=shopping.ref_id,
            friction_type=FrictionType.OUT_OF_STOCK,
            action=BoundedAction.REBUILD_BASKET,
            reason="Customer-accepted OOS replacement.",
            evidence_ref_ids=["EVD-REVAL"],
            candidate_skus=proposal.projected_skus,
            confidence=Decimal("0.92"),
            what="Apply replacement to a new basket version",
            why="Purchase plan changed",
            fix="Require a new approval for the new version.",
        ),
        intent=intent,
        persist=True,
    )
    if policy.decision is PolicyVerdict.BLOCK:
        return RescueDecision(
            accepted=False,
            stopped=True,
            reason=policy.reason_codes[0] if policy.reason_codes else "POLICY_BLOCK",
            original_basket_ref=original.ref_id,
            original_basket_version=original.version,
            policy_decision_ref=policy.ref_id,
        )

    new_basket = replace_item(db, original, proposal.failed_sku, proposal.candidate_sku)
    request = create_approval_request(
        db,
        shopping,
        new_basket,
        action_ref_id=action_ref_id or policy.action_ref_id,
        snapshot={
            "replacement_of": version_label(original),
            "failed_sku": proposal.failed_sku,
            "candidate_sku": proposal.candidate_sku,
        },
    )
    record_audit(
        db,
        session=shopping,
        actor=Actor.CUSTOMER.value,
        event_type="oos_rescue_accepted",
        decision="APPROVAL_REQUIRED",
        payload={
            "from": version_label(original),
            "to": version_label(new_basket),
            "approval_ref_id": request.ref_id,
        },
    )
    return RescueDecision(
        accepted=True,
        stopped=False,
        original_basket_ref=original.ref_id,
        original_basket_version=original.version,
        new_basket_ref=new_basket.ref_id,
        new_basket_version=new_basket.version,
        approval_ref_id=request.ref_id,
        policy_decision_ref=policy.ref_id,
        requires_customer_approval=True,
    )


def reject_oos_replacement(
    db: Session,
    shopping: ShoppingSession,
    proposal: RescueProposal,
) -> RescueDecision:
    """Customer declined. Original approved basket stays; checkout remains stopped."""
    record_audit(
        db,
        session=shopping,
        actor=Actor.CUSTOMER.value,
        event_type="oos_rescue_rejected",
        decision="STOPPED",
        payload={
            "basket_ref": f"{proposal.original_basket_ref}@v{proposal.original_basket_version}",
            "failed_sku": proposal.failed_sku,
            "candidate_sku": proposal.candidate_sku,
        },
    )
    return RescueDecision(
        accepted=False,
        stopped=True,
        reason="REPLACEMENT_REJECTED",
        original_basket_ref=proposal.original_basket_ref,
        original_basket_version=proposal.original_basket_version,
        requires_customer_approval=False,
    )


def _failed_sku(revalidation: RevalidationResult) -> str | None:
    for item in revalidation.checks:
        if item.name.value in {"INVENTORY_AVAILABLE", "QUANTITY_AVAILABLE"} and item.value:
            return item.value
        if item.reason_code == "OUT_OF_STOCK" and item.detail:
            return item.detail.split(",")[0].strip()
    return None


def _projected_skus(basket: Basket, failed_sku: str, candidate_sku: str) -> list[str]:
    skus: list[str] = []
    for item in basket.items:
        sku = item.variant.ref_id if item.variant is not None else None
        if sku is None:
            continue
        skus.append(candidate_sku if sku == failed_sku else sku)
    return skus


def _projected_total(db: Session, skus: list[str]) -> Decimal:
    total = Decimal("0")
    for sku in skus:
        variant = get_variant_by_sku(db, sku)
        if variant is None:
            continue
        total += effective_price(variant)
    return total


def _score(variant, failed, preferred_fits: list[str], style_tags: list[str], occasion_tags: list[str]) -> int:
    product = variant.product
    score = 0
    if variant.size == failed.size:
        score += 10
    if product.ref_id == failed.product.ref_id:
        score += 3
    if product.fit == failed.product.fit:
        score += 2
    if any(pref in {"relaxed_waist", "relaxed"} for pref in preferred_fits):
        if product.fit == "relaxed" or "relaxed_waist" in product.style_tags:
            score += 2
    for tag in style_tags:
        if tag in product.style_tags:
            score += 2
    for tag in occasion_tags:
        if tag in product.occasion_tags:
            score += 3
    return score
