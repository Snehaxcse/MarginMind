"""Deterministic Conversion Friction Resolver.

Rule-first. No LLM. No action selection (no GUIDE_CONFIDENCE, NO_UPSELL, etc.).

Signal taxonomy (SessionEventType values):
  product_viewed, size_guide_opened, product_compared, recommendation_rejected,
  rejection_reason_recorded, basket_updated, basket_over_hard_budget,
  size_unavailable_observed, product_oos_observed, choices_shown,
  checkout_started, checkout_abandoned, customer_message, fit_question_asked,
  price_question_asked.

Confidence (stable, not ML):
  1 supporting signal  → 0.45
  2 supporting signals → 0.65
  3 supporting signals → 0.84
  4+                   → 0.92
Commercial-truth rules (HARD budget overage, basket SKU OOS) use 0.92.

Rules (minimum evidence before a typed diagnosis):
  FIT_UNCERTAINTY     size_guide_opened ≥ 3
                      OR ≥ 2 of {size guide, fit comparison, fit question, fit rejection}
  BUDGET_MISMATCH     HARD budget exists AND live basket total > budget
                      (optional: rejection reason over_budget / too_expensive)
  PRICE_HESITATION    explicit price question OR rejection reason in {price, too_expensive}
                      never inferred from generic views or budget overage alone
  SIZE_UNAVAILABLE    size_unavailable_observed AND catalogue confirms size stock 0
  OUT_OF_STOCK        selected/approved basket SKU quantity available is 0
  CHOICE_OVERLOAD     choices_shown count ≥ 6 in one event
                      OR recommendation_rejected ≥ 4
                      OR (product_compared ≥ 3 AND recommendation_rejected ≥ 2)
  BASKET_INCOMPLETE   goal=complete_outfit AND basket has items but lacks
                      dress OR (trousers AND top)
  CHECKOUT_HESITATION checkout_started AND not approved/completed
                      (checkout_abandoned strengthens)
  STYLE_UNCERTAINTY   ≥ 2 explicit style rejections/dimension=style (conservative)
  COLOUR_UNCERTAINTY  ≥ 2 explicit colour rejections/dimension=colour (conservative)
  CATALOGUE_GAP       choices_shown with choice_count=0, or rejection not_in_catalogue

Insufficient evidence:
  no shopper signals and no basket issue → NONE
  some activity but no rule fires        → UNKNOWN

Every persisted diagnosis has ≥ 1 evidence_ref_id (EVT and/or EVD).
FIX is not returned.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.layers.basket import latest_basket_for_session, live_subtotal
from app.layers.catalogue import get_available_quantity, get_variant_by_sku, is_available
from app.layers.evidence import record_evidence
from app.layers.intent import latest_intent_for_session, shopper_intent_from_row
from app.layers.session import list_session_events
from app.models import Basket, FrictionDiagnosis, SessionEvent, ShoppingSession
from app.schemas.friction import (
    FrictionDiagnosisResult,
    FrictionEvaluation,
)
from app.schemas.intent import ShopperIntent
from app.schemas.vocabulary import (
    BudgetType,
    CheckoutState,
    EvidenceKind,
    FrictionType,
    ProductCategory,
    SessionEventType,
)

_CONFIDENCE = {
    1: Decimal("0.45"),
    2: Decimal("0.65"),
    3: Decimal("0.84"),
}
_TRUTH_CONFIDENCE = Decimal("0.92")
_MAX_CONFIDENCE = Decimal("0.92")

_FIT_REASONS = {"fit", "tight", "waist", "too_tight", "relaxed_waist"}
_PRICE_REASONS = {"price", "too_expensive", "costly", "expensive"}
_BUDGET_REASONS = {"over_budget", "too_expensive", "budget"}
_STYLE_REASONS = {"style", "not_my_style", "look"}
_COLOUR_REASONS = {"colour", "color", "wrong_colour", "wrong_color"}
_APPROVED_STATES = {
    CheckoutState.APPROVED_UNVERIFIED.value,
    CheckoutState.REVALIDATING.value,
    CheckoutState.READY_FOR_PAYMENT.value,
    CheckoutState.ORDER_CREATED.value,
    CheckoutState.PAYMENT_PENDING.value,
    CheckoutState.VERIFIED.value,
}

_TYPE_ORDER = [
    FrictionType.BUDGET_MISMATCH,
    FrictionType.OUT_OF_STOCK,
    FrictionType.SIZE_UNAVAILABLE,
    FrictionType.FIT_UNCERTAINTY,
    FrictionType.PRICE_HESITATION,
    FrictionType.CHECKOUT_HESITATION,
    FrictionType.BASKET_INCOMPLETE,
    FrictionType.CHOICE_OVERLOAD,
    FrictionType.CATALOGUE_GAP,
    FrictionType.STYLE_UNCERTAINTY,
    FrictionType.COLOUR_UNCERTAINTY,
    FrictionType.UNKNOWN,
    FrictionType.NONE,
]


def confidence_for_signal_count(count: int) -> Decimal:
    if count <= 0:
        return Decimal("0.00")
    if count >= 4:
        return _MAX_CONFIDENCE
    return _CONFIDENCE[count]


def diagnose_friction(
    db: Session,
    shopping: ShoppingSession,
    *,
    intent: ShopperIntent | None = None,
    persist: bool = True,
) -> FrictionEvaluation:
    events = list_session_events(db, shopping)
    resolved_intent = intent or _intent_from_session(db, shopping)
    basket = latest_basket_for_session(db, shopping)
    candidates = _collect_candidates(db, shopping, events, resolved_intent, basket)
    if not candidates:
        fallback = _none_or_unknown(db, shopping, events)
        candidates = [fallback]
    ranked = _rank(candidates)
    if persist:
        ranked = [_persist(db, shopping, item) for item in ranked]
    primary = ranked[0]
    secondary = ranked[1:]
    return FrictionEvaluation(
        session_ref_id=shopping.ref_id,
        primary=primary,
        secondary=secondary,
        diagnoses=ranked,
    )


def list_friction_diagnoses(db: Session, shopping: ShoppingSession) -> list[FrictionDiagnosis]:
    return list(
        db.scalars(
            select(FrictionDiagnosis)
            .where(FrictionDiagnosis.session_id == shopping.id)
            .order_by(FrictionDiagnosis.created_at.asc(), FrictionDiagnosis.ref_id.asc())
        ).all()
    )


def _intent_from_session(db: Session, shopping: ShoppingSession) -> ShopperIntent:
    row = latest_intent_for_session(db, shopping)
    if row is None:
        return ShopperIntent()
    return shopper_intent_from_row(row)


def _of(events: Iterable[SessionEvent], event_type: SessionEventType) -> list[SessionEvent]:
    value = event_type.value
    return [item for item in events if item.event_type == value]


def _reason(event: SessionEvent) -> str:
    raw = event.payload.get("reason") or ""
    return str(raw).strip().casefold()


def _dimension(event: SessionEvent) -> str:
    raw = event.payload.get("dimension") or ""
    return str(raw).strip().casefold()


def _refs(*groups: Iterable[SessionEvent]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for event in group:
            if event.ref_id not in seen:
                seen.append(event.ref_id)
            for extra in event.evidence_ref_ids or []:
                if extra not in seen:
                    seen.append(extra)
    return seen


def _candidate(
    friction_type: FrictionType,
    *,
    confidence: Decimal,
    evidence_ref_ids: list[str],
    reason_codes: list[str],
    summary: str,
    why: str,
    status: str = "active",
) -> FrictionDiagnosisResult:
    return FrictionDiagnosisResult(
        friction_type=friction_type,
        confidence=confidence,
        evidence_ref_ids=evidence_ref_ids,
        reason_codes=reason_codes,
        summary=summary,
        why=why,
        status=status,
    )


def _why(parts: list[str]) -> str:
    return "; ".join(part for part in parts if part)


def _collect_candidates(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
    intent: ShopperIntent,
    basket: Basket | None,
) -> list[FrictionDiagnosisResult]:
    found: list[FrictionDiagnosisResult] = []
    for builder in (
        lambda: _budget_mismatch(db, shopping, events, intent, basket),
        lambda: _out_of_stock(db, shopping, events, basket),
        lambda: _size_unavailable(db, shopping, events, intent),
        lambda: _fit_uncertainty(events),
        lambda: _price_hesitation(events),
        lambda: _checkout_hesitation(events, basket),
        lambda: _basket_incomplete(db, shopping, events, intent, basket),
        lambda: _choice_overload(events),
        lambda: _catalogue_gap(events),
        lambda: _style_uncertainty(events),
        lambda: _colour_uncertainty(events),
    ):
        result = builder()
        if result is not None:
            found.append(result)
    return found


def _fit_uncertainty(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    guides = _of(events, SessionEventType.SIZE_GUIDE_OPENED)
    compares = [
        item
        for item in _of(events, SessionEventType.PRODUCT_COMPARED)
        if _dimension(item) in {"fit", "waist"}
    ]
    questions = _of(events, SessionEventType.FIT_QUESTION_ASKED)
    rejections = [
        item
        for item in _of(events, SessionEventType.REJECTION_REASON_RECORDED)
        if _reason(item) in _FIT_REASONS or _dimension(item) == "fit"
    ]
    kinds = 0
    reasons: list[str] = []
    why_parts: list[str] = []
    if guides:
        kinds += 1
        if len(guides) >= 3:
            reasons.append("SIZE_GUIDE_REPEATED")
            why_parts.append(f"size guide opened {len(guides)} times ({guides[0].ref_id})")
        else:
            reasons.append("SIZE_GUIDE_OPENED")
            why_parts.append(f"size guide opened ({guides[0].ref_id})")
    if compares:
        kinds += 1
        reasons.append("FIT_COMPARISON")
        why_parts.append(f"compared fits ({compares[0].ref_id})")
    if questions:
        kinds += 1
        reasons.append("FIT_QUESTION")
        why_parts.append(f"asked a fit question ({questions[0].ref_id})")
    if rejections:
        kinds += 1
        reasons.append("FIT_REJECTION")
        why_parts.append(f"rejected a recommendation for fit ({rejections[0].ref_id})")

    strong_repeat = len(guides) >= 3
    if not strong_repeat and kinds < 2:
        return None
    signal_count = max(kinds, 3 if strong_repeat else 0)
    if strong_repeat:
        signal_count = max(signal_count, min(len(guides), 3) + (kinds - 1))
    return _candidate(
        FrictionType.FIT_UNCERTAINTY,
        confidence=confidence_for_signal_count(signal_count),
        evidence_ref_ids=_refs(guides, compares, questions, rejections),
        reason_codes=reasons,
        summary="Fit uncertainty appears to be preventing conversion.",
        why=_why(why_parts),
    )


def _budget_mismatch(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
    intent: ShopperIntent,
    basket: Basket | None,
) -> FrictionDiagnosisResult | None:
    if intent.budget.type != BudgetType.HARD or intent.budget.amount is None:
        return None
    if basket is None or not basket.items:
        return None
    total = live_subtotal(db, basket)
    if total <= intent.budget.amount:
        return None
    overage_events = _of(events, SessionEventType.BASKET_OVER_HARD_BUDGET)
    rejections = [
        item
        for item in _of(events, SessionEventType.REJECTION_REASON_RECORDED)
        if _reason(item) in _BUDGET_REASONS
    ]
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.BASKET_SNAPSHOT.value,
        summary=f"Basket total {total} exceeds HARD budget {intent.budget.amount}",
        payload={
            "subtotal": str(total),
            "budget_amount": str(intent.budget.amount),
            "budget_type": BudgetType.HARD.value,
            "basket_ref_id": basket.ref_id,
            "basket_version": basket.version,
        },
    )
    refs = _refs(overage_events, rejections)
    refs.append(snapshot.ref_id)
    reasons = ["HARD_BUDGET_EXCEEDED"]
    if rejections:
        reasons.append("EXPENSIVE_REJECTION")
    return _candidate(
        FrictionType.BUDGET_MISMATCH,
        confidence=_TRUTH_CONFIDENCE,
        evidence_ref_ids=refs,
        reason_codes=reasons,
        summary="The current basket exceeds the shopper's hard budget.",
        why=(
            f"live basket total {total} > HARD budget {intent.budget.amount} "
            f"({snapshot.ref_id})"
        ),
    )


def _price_hesitation(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    questions = _of(events, SessionEventType.PRICE_QUESTION_ASKED)
    rejections = [
        item
        for item in _of(events, SessionEventType.REJECTION_REASON_RECORDED)
        if _reason(item) in _PRICE_REASONS or _dimension(item) == "price"
    ]
    if not questions and not rejections:
        return None
    kinds = (1 if questions else 0) + (1 if rejections else 0)
    reasons: list[str] = []
    why_parts: list[str] = []
    if questions:
        reasons.append("EXPLICIT_PRICE_QUESTION")
        why_parts.append(f"asked a price question ({questions[0].ref_id})")
    if rejections:
        reasons.append("EXPLICIT_PRICE_REJECTION")
        why_parts.append(f"rejected an item on price ({rejections[0].ref_id})")
    return _candidate(
        FrictionType.PRICE_HESITATION,
        confidence=confidence_for_signal_count(kinds),
        evidence_ref_ids=_refs(questions, rejections),
        reason_codes=reasons,
        summary="Explicit price hesitation appears to be preventing conversion.",
        why=_why(why_parts),
    )


def _size_unavailable(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
    intent: ShopperIntent,
) -> FrictionDiagnosisResult | None:
    observed = _of(events, SessionEventType.SIZE_UNAVAILABLE_OBSERVED)
    confirmed: list[SessionEvent] = []
    details: list[str] = []
    for event in observed:
        sku = event.payload.get("sku")
        size = event.payload.get("size") or intent.usual_size
        if sku and not is_available(db, str(sku), 1):
            confirmed.append(event)
            details.append(f"{sku} unavailable ({event.ref_id})")
        elif sku and size:
            variant = get_variant_by_sku(db, str(sku))
            if variant is not None and variant.size == size and not is_available(db, str(sku), 1):
                confirmed.append(event)
                details.append(f"{sku} size {size} unavailable ({event.ref_id})")
    if not confirmed:
        return None
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.INVENTORY_SNAPSHOT.value,
        summary="Required size is unavailable for a relevant product",
        payload={"events": [item.ref_id for item in confirmed], "usual_size": intent.usual_size},
    )
    refs = _refs(confirmed)
    refs.append(snapshot.ref_id)
    return _candidate(
        FrictionType.SIZE_UNAVAILABLE,
        confidence=_TRUTH_CONFIDENCE,
        evidence_ref_ids=refs,
        reason_codes=["REQUIRED_SIZE_UNAVAILABLE"],
        summary="The shopper's required size is unavailable for a relevant product.",
        why=_why(details),
    )


def _out_of_stock(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
    basket: Basket | None,
) -> FrictionDiagnosisResult | None:
    oos_events = _of(events, SessionEventType.PRODUCT_OOS_OBSERVED)
    oos_skus: list[str] = []
    if basket is not None:
        for item in basket.items:
            sku = item.variant.ref_id if item.variant is not None else None
            if sku is None:
                continue
            if get_available_quantity(db, sku) <= 0:
                oos_skus.append(sku)
    confirmed_events = [
        event
        for event in oos_events
        if event.payload.get("sku") and get_available_quantity(db, str(event.payload["sku"])) <= 0
    ]
    if not oos_skus and not confirmed_events:
        return None
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.INVENTORY_SNAPSHOT.value,
        summary="Selected basket SKU is out of stock",
        payload={"skus": oos_skus, "event_ref_ids": [item.ref_id for item in confirmed_events]},
    )
    refs = _refs(confirmed_events)
    refs.append(snapshot.ref_id)
    return _candidate(
        FrictionType.OUT_OF_STOCK,
        confidence=_TRUTH_CONFIDENCE,
        evidence_ref_ids=refs,
        reason_codes=["BASKET_SKU_OOS"],
        summary="A selected basket SKU is out of stock.",
        why=f"out of stock: {', '.join(oos_skus) or confirmed_events[0].payload.get('sku')}",
    )


def _choice_overload(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    shown = _of(events, SessionEventType.CHOICES_SHOWN)
    comparisons = _of(events, SessionEventType.PRODUCT_COMPARED)
    rejections = _of(events, SessionEventType.RECOMMENDATION_REJECTED)
    high_choice = any((item.payload.get("choice_count") or 0) >= 6 for item in shown)
    many_rejects = len(rejections) >= 4
    compare_reject = len(comparisons) >= 3 and len(rejections) >= 2
    if not (high_choice or many_rejects or compare_reject):
        return None
    reasons: list[str] = []
    why_parts: list[str] = []
    kinds = 0
    if high_choice:
        kinds += 1
        reasons.append("MANY_CHOICES_SHOWN")
        why_parts.append(f"too many choices shown ({shown[0].ref_id})")
    if many_rejects or compare_reject:
        kinds += 1
        reasons.append("REPEATED_COMPARE_REJECT")
        if rejections:
            why_parts.append(f"repeated rejections ({rejections[0].ref_id})")
        if comparisons:
            why_parts.append(f"repeated comparisons ({comparisons[0].ref_id})")
    return _candidate(
        FrictionType.CHOICE_OVERLOAD,
        confidence=confidence_for_signal_count(max(kinds, 2)),
        evidence_ref_ids=_refs(shown, comparisons, rejections),
        reason_codes=reasons,
        summary="Too many options appear to be stalling a decision.",
        why=_why(why_parts),
    )


def _basket_incomplete(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
    intent: ShopperIntent,
    basket: Basket | None,
) -> FrictionDiagnosisResult | None:
    if intent.goal != "complete_outfit":
        return None
    if basket is None or not basket.items:
        return None
    categories = {item.variant.product.category for item in basket.items if item.variant}
    complete = ProductCategory.DRESSES.value in categories or (
        ProductCategory.TROUSERS.value in categories and ProductCategory.TOPS.value in categories
    )
    if complete:
        return None
    updates = _of(events, SessionEventType.BASKET_UPDATED)
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.BASKET_SNAPSHOT.value,
        summary="Basket is missing complete-outfit composition",
        payload={"categories": sorted(categories), "goal": intent.goal},
    )
    refs = _refs(updates)
    refs.append(snapshot.ref_id)
    return _candidate(
        FrictionType.BASKET_INCOMPLETE,
        confidence=confidence_for_signal_count(2 if updates else 1),
        evidence_ref_ids=refs,
        reason_codes=["MISSING_OUTFIT_COMPOSITION"],
        summary="The basket does not yet form a complete outfit.",
        why=f"categories present: {', '.join(sorted(categories)) or 'none'} ({snapshot.ref_id})",
    )


def _checkout_hesitation(
    events: list[SessionEvent],
    basket: Basket | None,
) -> FrictionDiagnosisResult | None:
    started = _of(events, SessionEventType.CHECKOUT_STARTED)
    if not started:
        return None
    if basket is not None and basket.status in _APPROVED_STATES:
        return None
    abandoned = _of(events, SessionEventType.CHECKOUT_ABANDONED)
    kinds = 1 + (1 if abandoned else 0)
    reasons = ["CHECKOUT_STARTED_INCOMPLETE"]
    why_parts = [f"checkout started ({started[0].ref_id})"]
    if abandoned:
        reasons.append("CHECKOUT_ABANDONED")
        why_parts.append(f"checkout abandoned ({abandoned[0].ref_id})")
    return _candidate(
        FrictionType.CHECKOUT_HESITATION,
        confidence=confidence_for_signal_count(kinds),
        evidence_ref_ids=_refs(started, abandoned),
        reason_codes=reasons,
        summary="Checkout was started but not completed.",
        why=_why(why_parts),
    )


def _style_uncertainty(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    matches = [
        item
        for item in _of(events, SessionEventType.REJECTION_REASON_RECORDED)
        if _reason(item) in _STYLE_REASONS or _dimension(item) == "style"
    ]
    compares = [
        item for item in _of(events, SessionEventType.PRODUCT_COMPARED) if _dimension(item) == "style"
    ]
    if len(matches) + len(compares) < 2:
        return None
    return _candidate(
        FrictionType.STYLE_UNCERTAINTY,
        confidence=confidence_for_signal_count(len(matches) + len(compares)),
        evidence_ref_ids=_refs(matches, compares),
        reason_codes=["EXPLICIT_STYLE_SIGNALS"],
        summary="Style uncertainty appears to be preventing conversion.",
        why=f"explicit style signals ({(matches or compares)[0].ref_id})",
    )


def _colour_uncertainty(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    matches = [
        item
        for item in _of(events, SessionEventType.REJECTION_REASON_RECORDED)
        if _reason(item) in _COLOUR_REASONS or _dimension(item) == "colour"
    ]
    compares = [
        item
        for item in _of(events, SessionEventType.PRODUCT_COMPARED)
        if _dimension(item) == "colour"
    ]
    if len(matches) + len(compares) < 2:
        return None
    return _candidate(
        FrictionType.COLOUR_UNCERTAINTY,
        confidence=confidence_for_signal_count(len(matches) + len(compares)),
        evidence_ref_ids=_refs(matches, compares),
        reason_codes=["EXPLICIT_COLOUR_SIGNALS"],
        summary="Colour uncertainty appears to be preventing conversion.",
        why=f"explicit colour signals ({(matches or compares)[0].ref_id})",
    )


def _catalogue_gap(events: list[SessionEvent]) -> FrictionDiagnosisResult | None:
    empty_shown = [
        item
        for item in _of(events, SessionEventType.CHOICES_SHOWN)
        if (item.payload.get("choice_count") or 0) == 0
    ]
    rejections = [
        item
        for item in _of(events, SessionEventType.RECOMMENDATION_REJECTED)
        if _reason(item) in {"not_in_catalogue", "catalogue_gap", "nothing_fits"}
    ]
    if not empty_shown and not rejections:
        return None
    return _candidate(
        FrictionType.CATALOGUE_GAP,
        confidence=confidence_for_signal_count(1 if not rejections else 2),
        evidence_ref_ids=_refs(empty_shown, rejections),
        reason_codes=["NO_ELIGIBLE_CATALOGUE_MATCH"],
        summary="The catalogue has no eligible match for this intent.",
        why="no eligible choices were available to show",
    )


def _shopper_signal_present(events: list[SessionEvent]) -> bool:
    ignored = {
        SessionEventType.INTENT_EXTRACTED.value,
        SessionEventType.PROVIDER_FAILED.value,
        SessionEventType.CUSTOMER_MESSAGE.value,
    }
    return any(item.event_type not in ignored for item in events)


def _none_or_unknown(
    db: Session,
    shopping: ShoppingSession,
    events: list[SessionEvent],
) -> FrictionDiagnosisResult:
    if _shopper_signal_present(events):
        friction = FrictionType.UNKNOWN
        status = "unknown"
        summary = "Session activity is present but is not enough to classify friction."
        why = "signals exist without a matching deterministic rule"
        reasons = ["INSUFFICIENT_EVIDENCE"]
    else:
        friction = FrictionType.NONE
        status = "none"
        summary = "No conversion friction is evidenced in this session."
        why = "no matching shopper signals or basket issues"
        reasons = ["NO_FRICTION_SIGNALS"]
    snapshot = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.FRICTION_EVALUATION.value,
        summary=summary,
        payload={
            "event_types": [item.event_type for item in events],
            "event_ref_ids": [item.ref_id for item in events],
            "friction_type": friction.value,
        },
    )
    return _candidate(
        friction,
        confidence=Decimal("0.80") if friction is FrictionType.NONE else Decimal("0.45"),
        evidence_ref_ids=[snapshot.ref_id],
        reason_codes=reasons,
        summary=summary,
        why=why,
        status=status,
    )


def _rank(candidates: list[FrictionDiagnosisResult]) -> list[FrictionDiagnosisResult]:
    order = {item: index for index, item in enumerate(_TYPE_ORDER)}
    typed = [item for item in candidates if item.friction_type not in {FrictionType.NONE, FrictionType.UNKNOWN}]
    rest = [item for item in candidates if item.friction_type in {FrictionType.NONE, FrictionType.UNKNOWN}]
    typed.sort(key=lambda item: (-item.confidence, order.get(item.friction_type, 99)))
    return typed or rest


def _persist(
    db: Session,
    shopping: ShoppingSession,
    result: FrictionDiagnosisResult,
) -> FrictionDiagnosisResult:
    row = FrictionDiagnosis(
        ref_id=next_numeric_ref_id(db, FrictionDiagnosis, RefPrefix.FRICTION),
        session_id=shopping.id,
        friction_type=result.friction_type.value,
        confidence=result.confidence,
        evidence_ref_ids=list(result.evidence_ref_ids),
        reason_codes=list(result.reason_codes),
        summary=result.summary,
        why=result.why,
        status=result.status,
    )
    db.add(row)
    db.flush()
    return result.model_copy(update={"ref_id": row.ref_id})
