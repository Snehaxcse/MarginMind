"""Growth Decision Engine: smallest useful bounded action. Proposal ≠ permission.

Friction → action (MVP):
  FIT_UNCERTAINTY      → GUIDE_CONFIDENCE
  STYLE_UNCERTAINTY    → BUILD_BASKET if goal=complete_outfit else RECOMMEND
  COLOUR_UNCERTAINTY   → GUIDE_CONFIDENCE
  BUDGET_MISMATCH      → rescue hierarchy, prefer REBUILD_BASKET
  PRICE_HESITATION     → rescue hierarchy; FIND_ALTERNATIVE / REBUILD_BASKET before any offer
  SIZE_UNAVAILABLE     → FIND_ALTERNATIVE
  OUT_OF_STOCK         → FIND_ALTERNATIVE
  CHOICE_OVERLOAD      → SIMPLIFY_CHOICES (≤ 3 real SKUs)
  BASKET_INCOMPLETE    → BUILD_BASKET
  CATALOGUE_GAP        → STOP
  CHECKOUT_HESITATION  → REQUEST_CHECKOUT only if the basket already validates
  NONE                 → STOP unless an explicit attach SKU would violate HARD budget
                         (then NO_UPSELL)
  UNKNOWN              → STOP (do not guess)

Rescue hierarchy (price / budget):
  1. cheaper compatible real SKU
  2. rebuild a complete look under HARD total
  3. remove optional accessory
  4. reference an existing seeded offer (never invent) — skipped when confidence < 0.70
  5. NO_UPSELL if the issue is an add-on; otherwise STOP

Does not execute, authorize, mutate baskets, or apply offers.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.layers.basket import (
    build_complete_looks,
    evaluate_optional_add_on,
    latest_basket_for_session,
    propose_replacement,
    validate_basket,
)
from app.layers.catalogue import (
    effective_price,
    filter_variants,
    get_available_quantity,
    get_variant_by_sku,
)
from app.layers.evidence import record_evidence
from app.layers.friction import diagnose_friction
from app.layers.intent import intent_to_catalogue_inputs, latest_intent_for_session, shopper_intent_from_row
from app.models import AgentAction, Basket, Offer, ShoppingSession
from app.schemas.action import ProposedAction
from app.schemas.basket import HARD_BUDGET_VIOLATION
from app.schemas.friction import FrictionDiagnosisResult, FrictionEvaluation
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import (
    ActionStatus,
    BoundedAction,
    BudgetType,
    EvidenceKind,
    FrictionType,
    ProductCategory,
)

DEMO_ATTACH_SKU = "SKU-012-OS"
_DISCOUNT_CONFIDENCE_FLOOR = Decimal("0.70")
_SEEDED_OFFER_PREFIX = "OFR-"

_APPROVAL_ACTIONS = {
    BoundedAction.BUILD_BASKET,
    BoundedAction.REBUILD_BASKET,
    BoundedAction.APPLY_AUTHORIZED_OFFER,
    BoundedAction.REQUEST_CHECKOUT,
}


def propose_growth_action(
    db: Session,
    shopping: ShoppingSession,
    *,
    intent: ShopperIntent | None = None,
    diagnosis: FrictionEvaluation | None = None,
    persist: bool = True,
    upsell_sku: str | None = None,
) -> ProposedAction:
    resolved_intent = intent or _intent_from_session(db, shopping)
    evaluation = diagnosis or diagnose_friction(db, shopping, intent=resolved_intent)
    primary = evaluation.primary
    basket = latest_basket_for_session(db, shopping)
    draft = _decide(db, shopping, resolved_intent, primary, basket, upsell_sku=upsell_sku)
    draft = draft.model_copy(
        update={
            "session_ref_id": shopping.ref_id,
            "friction_ref_id": primary.ref_id,
            "friction_type": primary.friction_type,
            "confidence": primary.confidence,
            "requires_policy_check": True,
            "status": ActionStatus.PROPOSED,
            "what": primary.summary,
            "why": primary.why,
        }
    )
    if persist:
        draft = _persist(db, shopping, draft)
    return draft


def list_agent_actions(db: Session, shopping: ShoppingSession) -> list[AgentAction]:
    return list(
        db.scalars(
            select(AgentAction)
            .where(AgentAction.session_id == shopping.id)
            .order_by(AgentAction.created_at.asc(), AgentAction.ref_id.asc())
        ).all()
    )


def _intent_from_session(db: Session, shopping: ShoppingSession) -> ShopperIntent:
    row = latest_intent_for_session(db, shopping)
    if row is None:
        return ShopperIntent()
    return shopper_intent_from_row(row)


def _decide(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
    basket: Basket | None,
    *,
    upsell_sku: str | None,
) -> ProposedAction:
    friction = primary.friction_type
    if friction is FrictionType.FIT_UNCERTAINTY:
        return _action(
            BoundedAction.GUIDE_CONFIDENCE,
            primary,
            reason="Shopper appears uncertain about fit.",
            reason_codes=["FIT_GUIDANCE"],
            fix="Offer a concise fit comparison of the items under consideration.",
        )
    if friction is FrictionType.COLOUR_UNCERTAINTY:
        return _action(
            BoundedAction.GUIDE_CONFIDENCE,
            primary,
            reason="Shopper appears uncertain about colour.",
            reason_codes=["COLOUR_GUIDANCE"],
            fix="Compare colour options already in the eligible catalogue.",
        )
    if friction is FrictionType.STYLE_UNCERTAINTY:
        if intent.goal == "complete_outfit":
            return _build_basket(db, shopping, intent, primary)
        return _recommend(db, shopping, intent, primary)
    if friction is FrictionType.BUDGET_MISMATCH:
        return _rescue(db, shopping, intent, primary, basket, mismatch=True)
    if friction is FrictionType.PRICE_HESITATION:
        return _rescue(db, shopping, intent, primary, basket, mismatch=False)
    if friction is FrictionType.SIZE_UNAVAILABLE:
        return _find_alternative(db, shopping, intent, primary, basket, size_unavailable=True)
    if friction is FrictionType.OUT_OF_STOCK:
        return _find_alternative(db, shopping, intent, primary, basket, size_unavailable=False)
    if friction is FrictionType.CHOICE_OVERLOAD:
        return _simplify(db, shopping, intent, primary)
    if friction is FrictionType.BASKET_INCOMPLETE:
        return _build_basket(db, shopping, intent, primary)
    if friction is FrictionType.CATALOGUE_GAP:
        return _stop(primary, reason="No eligible catalogue match exists.", reason_codes=["CATALOGUE_GAP"])
    if friction is FrictionType.CHECKOUT_HESITATION:
        return _checkout_or_repair(db, shopping, intent, primary, basket)
    if friction is FrictionType.UNKNOWN:
        return _stop(
            primary,
            reason="Evidence is insufficient; guessing would be unsafe.",
            reason_codes=["INSUFFICIENT_EVIDENCE"],
        )
    if upsell_sku:
        no_upsell = _no_upsell_if_attach_blocked(db, shopping, basket, intent, primary, upsell_sku)
        if no_upsell is not None:
            return no_upsell
    return _stop(primary, reason="No conversion friction is evidenced.", reason_codes=["NO_INTERVENTION"])


def _action(
    action: BoundedAction,
    primary: FrictionDiagnosisResult,
    *,
    reason: str,
    reason_codes: list[str],
    fix: str,
    candidate_skus: list[str] | None = None,
    offer_ref_id: str | None = None,
    extra_evidence: list[str] | None = None,
    potential_revenue_not_pursued: Decimal | None = None,
) -> ProposedAction:
    evidence = list(primary.evidence_ref_ids)
    for ref in extra_evidence or []:
        if ref not in evidence:
            evidence.append(ref)
    return ProposedAction(
        session_ref_id="",
        friction_type=primary.friction_type,
        action=action,
        reason=reason,
        reason_codes=reason_codes,
        evidence_ref_ids=evidence,
        candidate_skus=list(candidate_skus or []),
        offer_ref_id=offer_ref_id,
        confidence=primary.confidence,
        requires_policy_check=True,
        requires_customer_approval=action in _APPROVAL_ACTIONS,
        potential_revenue_not_pursued=potential_revenue_not_pursued,
        status=ActionStatus.PROPOSED,
        what=primary.summary,
        why=primary.why,
        fix=fix,
    )


def _stop(primary: FrictionDiagnosisResult, *, reason: str, reason_codes: list[str]) -> ProposedAction:
    return _action(
        BoundedAction.STOP,
        primary,
        reason=reason,
        reason_codes=reason_codes,
        fix="Do not intervene further until new evidence or inventory appears.",
    )


def _eligible(db: Session, shopping: ShoppingSession, intent: ShopperIntent):
    constraints, soft = intent_to_catalogue_inputs(intent, merchant_id=shopping.merchant_id)
    return filter_variants(db, constraints, soft=soft)


def _look_skus(db: Session, shopping: ShoppingSession, intent: ShopperIntent, *, limit: int = 3) -> list[str]:
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id, limit=limit)
    skus: list[str] = []
    for look in looks:
        for sku in look.skus:
            if sku not in skus:
                skus.append(sku)
            if len(skus) >= 3:
                return skus
    return skus


def _build_basket(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
) -> ProposedAction:
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id, limit=1)
    if not looks:
        return _stop(primary, reason="No complete look fits hard constraints.", reason_codes=["NO_VALID_LOOK"])
    return _action(
        BoundedAction.BUILD_BASKET,
        primary,
        reason="Complete the outfit from real in-stock SKUs within the hard budget.",
        reason_codes=["COMPLETE_OUTFIT"],
        fix="Propose a complete look. Do not mutate the current basket until the customer approves.",
        candidate_skus=list(looks[0].skus),
    )


def _recommend(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
) -> ProposedAction:
    skus = [item.ref_id for item in _eligible(db, shopping, intent)[:3]]
    if not skus:
        return _stop(primary, reason="No eligible SKUs to recommend.", reason_codes=["NO_ELIGIBLE_SKUS"])
    return _action(
        BoundedAction.RECOMMEND,
        primary,
        reason="Show a small set of eligible catalogue options.",
        reason_codes=["ELIGIBLE_RECOMMENDATION"],
        fix="Present up to three real in-stock SKUs. Do not invent products.",
        candidate_skus=skus,
    )


def _simplify(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
) -> ProposedAction:
    skus = _look_skus(db, shopping, intent, limit=3)
    if not skus:
        skus = [item.ref_id for item in _eligible(db, shopping, intent)[:3]]
    if not skus:
        return _stop(primary, reason="Nothing remains to simplify.", reason_codes=["NO_ELIGIBLE_SKUS"])
    return _action(
        BoundedAction.SIMPLIFY_CHOICES,
        primary,
        reason="Too many options are stalling the decision.",
        reason_codes=["REDUCE_TO_THREE"],
        fix="Reduce the choice set to at most three real catalogue options.",
        candidate_skus=skus[:3],
    )


def _checkout_or_repair(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
    basket: Basket | None,
) -> ProposedAction:
    if basket is None or not basket.items:
        return _build_basket(db, shopping, intent, primary)
    result = validate_basket(db, basket, intent)
    if result.valid:
        return _action(
            BoundedAction.REQUEST_CHECKOUT,
            primary,
            reason="The current basket is valid; checkout can be requested after approval.",
            reason_codes=["BASKET_VALID"],
            fix="Request checkout for this exact basket version. Do not charge yet.",
        )
    if FrictionType.BASKET_INCOMPLETE.value in str(result.reasons) or intent.goal == "complete_outfit":
        categories = {item.variant.product.category for item in basket.items if item.variant}
        complete = ProductCategory.DRESSES.value in categories or (
            ProductCategory.TROUSERS.value in categories and ProductCategory.TOPS.value in categories
        )
        if not complete:
            return _build_basket(db, shopping, intent, primary)
    if result.hard_budget_pass is False:
        return _rescue(db, shopping, intent, primary, basket, mismatch=True)
    return _stop(primary, reason="Checkout is not appropriate until the basket validates.", reason_codes=["BASKET_INVALID"])


def _cheaper_swaps(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    basket: Basket,
) -> list[tuple[str, str, Decimal]]:
    basket_skus = {item.variant.ref_id for item in basket.items if item.variant}
    eligible = _eligible(db, shopping, intent)
    found: list[tuple[str, str, Decimal]] = []
    for item in basket.items:
        if item.variant is None:
            continue
        current_price = effective_price(item.variant)
        category = item.variant.product.category
        for candidate in eligible:
            if candidate.ref_id in basket_skus:
                continue
            if candidate.product.category != category:
                continue
            if effective_price(candidate) >= current_price:
                continue
            proposal = propose_replacement(
                db, basket, replace_sku=item.variant.ref_id, candidate_sku=candidate.ref_id, intent=intent
            )
            if proposal.acceptable and proposal.resulting_subtotal is not None:
                found.append((item.variant.ref_id, candidate.ref_id, proposal.resulting_subtotal))
    found.sort(key=lambda row: (row[2], row[1]))
    optional = [
        row
        for row in found
        if (variant := get_variant_by_sku(db, row[0])) is not None
        and variant.product.category == ProductCategory.ACCESSORIES.value
    ]
    return optional or found


def _rebuild_look(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    basket: Basket | None,
) -> list[str]:
    looks = build_complete_looks(db, intent, merchant_id=shopping.merchant_id, limit=3)
    current = {item.variant.ref_id for item in basket.items if item.variant} if basket else set()
    for look in looks:
        if current and set(look.skus) == current:
            continue
        return list(look.skus)
    if looks:
        return list(looks[0].skus)
    return []


def _remove_optional(
    db: Session,
    intent: ShopperIntent,
    basket: Basket,
) -> list[str] | None:
    remaining = [
        item.variant.ref_id
        for item in basket.items
        if item.variant and item.variant.product.category != ProductCategory.ACCESSORIES.value
    ]
    if len(remaining) == len(list(basket.items)) or not remaining:
        return None
    total = Decimal("0")
    for sku in remaining:
        variant = get_variant_by_sku(db, sku)
        if variant is None:
            return None
        total += effective_price(variant)
    if intent.budget.type == BudgetType.HARD and intent.budget.amount is not None:
        if total > intent.budget.amount:
            return None
    return remaining


def _seeded_offer_ref(db: Session, shopping: ShoppingSession) -> str | None:
    offer = db.scalar(
        select(Offer)
        .where(Offer.merchant_id == shopping.merchant_id, Offer.is_active.is_(True))
        .order_by(Offer.ref_id.asc())
    )
    if offer is None or not offer.ref_id.startswith(_SEEDED_OFFER_PREFIX):
        return None
    return offer.ref_id


def _rescue(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
    basket: Basket | None,
    *,
    mismatch: bool,
) -> ProposedAction:
    if basket is None or not basket.items:
        look = _rebuild_look(db, shopping, intent, basket)
        if look:
            return _action(
                BoundedAction.REBUILD_BASKET if mismatch else BoundedAction.FIND_ALTERNATIVE,
                primary,
                reason="Rebuild from cheaper real SKUs inside the hard budget.",
                reason_codes=["REBUILD_FROM_CATALOGUE"],
                fix="Propose a rebuilt look. Do not mutate the basket until approval.",
                candidate_skus=look,
            )
        return _stop(primary, reason="No valid cheaper basket exists.", reason_codes=["NO_VALID_REBUILD"])

    swaps = _cheaper_swaps(db, shopping, intent, basket)
    if swaps:
        _replace, candidate, _total = swaps[0]
        action = BoundedAction.REBUILD_BASKET if mismatch else BoundedAction.FIND_ALTERNATIVE
        return _action(
            action,
            primary,
            reason="A cheaper compatible real SKU can keep the basket within constraints.",
            reason_codes=["CHEAPER_EQUIVALENT"],
            fix="Show the cheaper SKU as a replacement candidate. Do not swap the line yet.",
            candidate_skus=[candidate],
        )

    look = _rebuild_look(db, shopping, intent, basket)
    if look:
        return _action(
            BoundedAction.REBUILD_BASKET,
            primary,
            reason="A complete look can be rebuilt from real SKUs within the hard budget.",
            reason_codes=["REBUILD_FROM_CATALOGUE"],
            fix="Propose the rebuilt look. Do not mutate the current basket until approval.",
            candidate_skus=look,
        )

    trimmed = _remove_optional(db, intent, basket)
    if trimmed:
        return _action(
            BoundedAction.REBUILD_BASKET,
            primary,
            reason="Removing an optional accessory would satisfy the hard budget.",
            reason_codes=["REMOVE_OPTIONAL_ITEM"],
            fix="Propose dropping the optional add-on. Do not mutate the basket yet.",
            candidate_skus=trimmed,
        )

    if not mismatch and primary.confidence >= _DISCOUNT_CONFIDENCE_FLOOR:
        offer_ref = _seeded_offer_ref(db, shopping)
        if offer_ref is not None:
            return _action(
                BoundedAction.APPLY_AUTHORIZED_OFFER,
                primary,
                reason="Non-discount rescues are exhausted; an existing merchant offer may be considered.",
                reason_codes=["SEEDED_OFFER_CANDIDATE"],
                fix="Policy must still authorize this offer. Do not apply it here.",
                offer_ref_id=offer_ref,
            )

    if mismatch:
        return _stop(primary, reason="No valid rebuild exists inside the hard budget.", reason_codes=["NO_VALID_REBUILD"])
    return _action(
        BoundedAction.NO_UPSELL,
        primary,
        reason="No cheaper real alternative remains; do not invent a discount.",
        reason_codes=["NO_VALID_REBUILD"],
        fix="Do not upsell or invent an offer.",
    )


def _find_alternative(
    db: Session,
    shopping: ShoppingSession,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
    basket: Basket | None,
    *,
    size_unavailable: bool,
) -> ProposedAction:
    eligible = _eligible(db, shopping, intent)
    candidates: list[str] = []
    if basket is not None:
        problem_skus = []
        for item in basket.items:
            if item.variant is None:
                continue
            sku = item.variant.ref_id
            if get_available_quantity(db, sku) <= 0:
                problem_skus.append(sku)
            elif size_unavailable and intent.usual_size and item.variant.size != intent.usual_size:
                problem_skus.append(sku)
        for sku in problem_skus or [item.variant.ref_id for item in basket.items if item.variant]:
            variant = get_variant_by_sku(db, sku)
            if variant is None:
                continue
            for candidate in eligible:
                if candidate.ref_id == sku:
                    continue
                if candidate.product.category != variant.product.category:
                    continue
                proposal = propose_replacement(
                    db, basket, replace_sku=sku, candidate_sku=candidate.ref_id, intent=intent
                )
                if proposal.acceptable:
                    candidates.append(candidate.ref_id)
                if len(candidates) >= 3:
                    break
            if len(candidates) >= 3:
                break
    if not candidates:
        candidates = [item.ref_id for item in eligible[:3]]
    if not candidates:
        return _stop(primary, reason="No in-stock alternative satisfies hard constraints.", reason_codes=["NO_VALID_ALTERNATIVE"])
    return _action(
        BoundedAction.FIND_ALTERNATIVE,
        primary,
        reason="A real in-stock alternative can be shown without changing the basket.",
        reason_codes=["INVENTORY_ALTERNATIVE"],
        fix="Show replacement candidates. Actual replacement requires later customer approval.",
        candidate_skus=candidates[:3],
    )


def _no_upsell_if_attach_blocked(
    db: Session,
    shopping: ShoppingSession,
    basket: Basket | None,
    intent: ShopperIntent,
    primary: FrictionDiagnosisResult,
    sku: str,
) -> ProposedAction | None:
    if basket is None or not basket.items:
        return None
    evaluation = evaluate_optional_add_on(db, basket, sku, intent)
    if evaluation.allowed or evaluation.reason != HARD_BUDGET_VIOLATION:
        return None
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.BASKET_SNAPSHOT.value,
        summary=f"Attach {sku} would exceed HARD budget",
        payload={
            "sku": sku,
            "current_subtotal": str(evaluation.current_subtotal),
            "candidate_price": str(evaluation.candidate_price),
            "resulting_subtotal": str(evaluation.resulting_subtotal),
        },
    )
    return _action(
        BoundedAction.NO_UPSELL,
        primary,
        reason="Attaching the accessory would violate the shopper's hard budget.",
        reason_codes=[HARD_BUDGET_VIOLATION],
        fix="Do not attach the item. Respect the hard budget.",
        extra_evidence=[snapshot.ref_id],
        potential_revenue_not_pursued=evaluation.candidate_price,
    )


def _persist(db: Session, shopping: ShoppingSession, proposal: ProposedAction) -> ProposedAction:
    row = AgentAction(
        ref_id=next_numeric_ref_id(db, AgentAction, RefPrefix.ACTION),
        session_id=shopping.id,
        friction_ref_id=proposal.friction_ref_id,
        action=proposal.action.value,
        reason=proposal.reason,
        reason_codes=list(proposal.reason_codes),
        evidence_ref_ids=list(proposal.evidence_ref_ids),
        candidate_skus=list(proposal.candidate_skus),
        offer_ref_id=proposal.offer_ref_id,
        confidence=proposal.confidence,
        requires_policy_check=True,
        requires_customer_approval=proposal.requires_customer_approval,
        potential_revenue_not_pursued=proposal.potential_revenue_not_pursued,
        what=proposal.what,
        why=proposal.why,
        fix=proposal.fix,
        status=ActionStatus.PROPOSED.value,
    )
    db.add(row)
    db.flush()
    return proposal.model_copy(update={"ref_id": row.ref_id})
