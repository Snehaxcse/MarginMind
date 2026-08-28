"""Final revalidation of an exact approved basket.

Approval is not success. This module re-reads catalogue, inventory, offers,
margin, and approval binding immediately before a future checkout. It never
executes payment, never mutates the approved snapshot, and never trusts a
prior PolicyDecision or GDE total.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.engines.policy import load_merchant_policies, validate_action
from app.layers.approval import get_approval, version_approval_covers
from app.layers.basket import get_basket, latest_basket_for_session, version_label
from app.layers.catalogue import effective_price, get_available_quantity, get_variant_by_sku
from app.layers.evidence import record_audit, record_evidence
from app.layers.intent import latest_intent_for_session, shopper_intent_from_row
from app.models import (
    Approval,
    Basket,
    Offer,
    RevalidationResult as RevalidationResultRow,
    ShoppingSession,
)
from app.schemas.action import ProposedAction
from app.schemas.basket import HARD_BUDGET_VIOLATION
from app.schemas.intent import ShopperIntent
from app.schemas.revalidation import RevalidationCheckResult, RevalidationResult
from app.schemas.vocabulary import (
    Actor,
    ApprovalStatus,
    BoundedAction,
    BudgetType,
    CheckStatus,
    CheckoutState,
    EvidenceKind,
    FrictionType,
    PolicyCheckName,
    PolicyVerdict,
    RevalidationCheckName,
    RevalidationStatus,
)

_STOP_ON = {
    RevalidationCheckName.BASKET_VERSION_VALID,
    RevalidationCheckName.CUSTOMER_APPROVAL_VALID,
    RevalidationCheckName.OFFER_EXISTS,
    RevalidationCheckName.OFFER_ACTIVE,
    RevalidationCheckName.OFFER_ELIGIBILITY,
}


def revalidate_approved_basket(
    db: Session,
    shopping: ShoppingSession,
    approval_ref_id: str,
    *,
    basket: Basket | None = None,
    intent: ShopperIntent | None = None,
    persist: bool = True,
) -> RevalidationResult:
    """Re-check the exact basket version bound to `approval_ref_id`.

    If `basket` is supplied and does not match that version, the approval is
    stale: STOPPED with STALE_APPROVAL / BASKET_VERSION_MISMATCH.
    """
    now = datetime.now(timezone.utc)
    approval = get_approval(db, approval_ref_id)
    resolved_intent = intent or _intent(db, shopping)
    checks: list[RevalidationCheckResult] = []
    changed: list[str] = []

    if approval is None:
        checks = _mismatch_checks("UNKNOWN_APPROVAL", "Approval was not found.")
        return _finalize(
            db,
            shopping,
            approval_ref_id=approval_ref_id,
            approved=None,
            target=None,
            checks=checks,
            changed=changed,
            now=now,
            persist=persist,
            fingerprint=_commercial_fingerprint(db, None, None, None, stale=True, extra="missing"),
        )

    approved_basket = get_basket(db, approval.basket.ref_id, version=approval.basket_version)
    if approved_basket is None:
        approved_basket = db.get(Basket, approval.basket_id)

    claimed = basket if basket is not None else latest_basket_for_session(db, shopping)
    version_ok = True
    if claimed is not None and approved_basket is not None:
        if claimed.id != approved_basket.id or claimed.version != approved_basket.version:
            version_ok = False
    if claimed is not None and (
        claimed.ref_id != approval.basket.ref_id or claimed.version != approval.basket_version
    ):
        version_ok = False

    if not version_ok:
        fingerprint = _commercial_fingerprint(
            db, approval, approved_basket, (approval.snapshot or {}).get("offer_ref_id"), stale=True
        )
        existing = _existing(db, approval.ref_id, fingerprint)
        if existing is not None:
            return _row_to_schema(existing, shopping.ref_id, reused=True)
        checks = _mismatch_checks(
            "STALE_APPROVAL",
            (
                f"{approval.ref_id} authorizes {approval.basket.ref_id}@v{approval.basket_version}; "
                f"workflow referenced {version_label(claimed) if claimed is not None else 'unknown'}."
            ),
        )
        result = _finalize(
            db,
            shopping,
            approval_ref_id=approval.ref_id,
            approved=approval,
            target=approved_basket,
            checks=checks,
            changed=["approval"],
            now=now,
            persist=persist,
            fingerprint=fingerprint,
        )
        return result

    target = approved_basket
    snapshot_skus = list((approval.snapshot or {}).get("skus") or [])
    offer_ref_id = (approval.snapshot or {}).get("offer_ref_id")
    fingerprint = _commercial_fingerprint(
        db, approval, target, offer_ref_id, stale=False, intent=resolved_intent
    )
    existing = _existing(db, approval.ref_id, fingerprint)
    if existing is not None:
        return _row_to_schema(existing, shopping.ref_id, reused=True)

    checks.append(_approval_valid(db, shopping, approval, target))
    checks.append(_version_valid(approval, target, claimed))
    sku_checks, live_total, line_changed = _line_checks(db, target, snapshot_skus)
    checks.extend(sku_checks)
    changed.extend(line_changed)
    checks.append(_hard_budget_check(resolved_intent, live_total, sku_checks))
    if checks[-1].status is CheckStatus.FAIL:
        changed.append("budget")
    if target is None:
        offer_checks = [
            _na(RevalidationCheckName.OFFER_EXISTS),
            _na(RevalidationCheckName.OFFER_ACTIVE),
            _na(RevalidationCheckName.OFFER_ELIGIBILITY),
            _na(RevalidationCheckName.MARGIN_VALID),
            _na(RevalidationCheckName.MERCHANT_POLICY_VALID),
        ]
        offer_changed = []
    else:
        offer_checks, offer_changed = _offer_and_policy(
            db, shopping, target, resolved_intent, approval, offer_ref_id
        )
    checks.extend(offer_checks)
    changed.extend(offer_changed)

    fingerprint = _commercial_fingerprint(
        db, approval, target, offer_ref_id, stale=False, intent=resolved_intent
    )
    result = _finalize(
        db,
        shopping,
        approval_ref_id=approval.ref_id,
        approved=approval,
        target=target,
        checks=checks,
        changed=changed,
        now=now,
        persist=persist,
        fingerprint=fingerprint,
        offer_ref_id=offer_ref_id,
        resulting_subtotal=live_total,
    )
    if persist and result.status is not RevalidationStatus.PASS and target is not None:
        if target.status != CheckoutState.REVALIDATION_FAILED.value:
            target.status = CheckoutState.REVALIDATION_FAILED.value
            db.flush()
    return result


def get_revalidation(db: Session, ref_id: str) -> RevalidationResultRow | None:
    return db.scalar(select(RevalidationResultRow).where(RevalidationResultRow.ref_id == ref_id))


def list_revalidations(db: Session, shopping: ShoppingSession) -> list[RevalidationResultRow]:
    return list(
        db.scalars(
            select(RevalidationResultRow)
            .where(RevalidationResultRow.session_id == shopping.id)
            .order_by(RevalidationResultRow.validated_at.asc(), RevalidationResultRow.ref_id.asc())
        ).all()
    )


def _intent(db: Session, shopping: ShoppingSession) -> ShopperIntent:
    row = latest_intent_for_session(db, shopping)
    if row is None:
        return ShopperIntent()
    return shopper_intent_from_row(row)


def _na(name: RevalidationCheckName, detail: str | None = None) -> RevalidationCheckResult:
    return RevalidationCheckResult(name=name, status=CheckStatus.NA, detail=detail)


def _pass(name: RevalidationCheckName, *, detail: str | None = None, value: str | None = None) -> RevalidationCheckResult:
    return RevalidationCheckResult(name=name, status=CheckStatus.PASS, detail=detail, value=value)


def _fail(
    name: RevalidationCheckName,
    reason_code: str,
    *,
    detail: str | None = None,
    value: str | None = None,
) -> RevalidationCheckResult:
    return RevalidationCheckResult(
        name=name,
        status=CheckStatus.FAIL,
        reason_code=reason_code,
        detail=detail,
        value=value,
    )


def _mismatch_checks(reason: str, detail: str) -> list[RevalidationCheckResult]:
    version = _fail(RevalidationCheckName.BASKET_VERSION_VALID, reason, detail=detail)
    approval = _fail(RevalidationCheckName.CUSTOMER_APPROVAL_VALID, reason, detail=detail)
    unused = [
        RevalidationCheckName.SKU_EXISTS,
        RevalidationCheckName.PRODUCT_ACTIVE,
        RevalidationCheckName.VARIANT_ACTIVE,
        RevalidationCheckName.CORRECT_VARIANT,
        RevalidationCheckName.INVENTORY_AVAILABLE,
        RevalidationCheckName.QUANTITY_AVAILABLE,
        RevalidationCheckName.PRICE_UNCHANGED,
        RevalidationCheckName.HARD_BUDGET,
        RevalidationCheckName.OFFER_EXISTS,
        RevalidationCheckName.OFFER_ACTIVE,
        RevalidationCheckName.OFFER_ELIGIBILITY,
        RevalidationCheckName.MARGIN_VALID,
        RevalidationCheckName.MERCHANT_POLICY_VALID,
    ]
    return [version, approval, *[_na(name, "Approval/version mismatch.") for name in unused]]


def _approval_valid(
    db: Session,
    shopping: ShoppingSession,
    approval: Approval,
    target: Basket | None,
) -> RevalidationCheckResult:
    if approval.session_id != shopping.id:
        return _fail(
            RevalidationCheckName.CUSTOMER_APPROVAL_VALID,
            "STALE_APPROVAL",
            detail="Approval does not belong to this session.",
        )
    if approval.status != ApprovalStatus.GRANTED.value:
        return _fail(
            RevalidationCheckName.CUSTOMER_APPROVAL_VALID,
            "CUSTOMER_APPROVAL_REQUIRED",
            detail=f"Approval is {approval.status}, not granted.",
        )
    if target is None:
        return _fail(RevalidationCheckName.CUSTOMER_APPROVAL_VALID, "BASKET_MISSING")
    if not version_approval_covers(db, shopping, target, action_ref_id=approval.action_ref_id):
        return _fail(
            RevalidationCheckName.CUSTOMER_APPROVAL_VALID,
            "STALE_APPROVAL",
            detail="Granted approval does not cover this exact basket version.",
        )
    return _pass(
        RevalidationCheckName.CUSTOMER_APPROVAL_VALID,
        value=approval.ref_id,
        detail=version_label(target),
    )


def _version_valid(
    approval: Approval,
    target: Basket | None,
    claimed: Basket | None,
) -> RevalidationCheckResult:
    if target is None:
        return _fail(RevalidationCheckName.BASKET_VERSION_VALID, "BASKET_MISSING")
    if target.ref_id != approval.basket.ref_id or target.version != approval.basket_version:
        return _fail(
            RevalidationCheckName.BASKET_VERSION_VALID,
            "BASKET_VERSION_MISMATCH",
            detail=f"Approved {approval.basket.ref_id}@v{approval.basket_version}.",
        )
    if claimed is not None and (claimed.id != target.id or claimed.version != target.version):
        return _fail(
            RevalidationCheckName.BASKET_VERSION_VALID,
            "BASKET_VERSION_MISMATCH",
            detail=f"Workflow {version_label(claimed)} is not {version_label(target)}.",
        )
    return _pass(RevalidationCheckName.BASKET_VERSION_VALID, value=version_label(target))


def _line_checks(
    db: Session,
    target: Basket | None,
    snapshot_skus: list[str],
) -> tuple[list[RevalidationCheckResult], Decimal | None, list[str]]:
    names_empty = [
        RevalidationCheckName.SKU_EXISTS,
        RevalidationCheckName.PRODUCT_ACTIVE,
        RevalidationCheckName.VARIANT_ACTIVE,
        RevalidationCheckName.CORRECT_VARIANT,
        RevalidationCheckName.INVENTORY_AVAILABLE,
        RevalidationCheckName.QUANTITY_AVAILABLE,
        RevalidationCheckName.PRICE_UNCHANGED,
    ]
    if target is None or not target.items:
        return (
            [_fail(name, "BASKET_MISSING") if name is RevalidationCheckName.SKU_EXISTS else _na(name) for name in names_empty],
            None,
            [],
        )

    missing: list[str] = []
    inactive_products: list[str] = []
    inactive_variants: list[str] = []
    wrong_variant: list[str] = []
    oos: list[str] = []
    qty_fail: list[str] = []
    price_changed: list[str] = []
    live_total = Decimal("0")
    live_skus: list[str] = []
    changed: list[str] = []

    for item in target.items:
        sku = item.variant.ref_id if item.variant is not None else None
        if sku is None:
            missing.append("UNKNOWN")
            continue
        live_skus.append(sku)
        live = get_variant_by_sku(db, sku)
        if live is None:
            missing.append(sku)
            continue
        if live.id != item.variant_id:
            wrong_variant.append(sku)
        if live.product is None or not live.product.is_active:
            inactive_products.append(sku)
        if not live.is_active:
            inactive_variants.append(sku)
        available = get_available_quantity(db, sku)
        if available <= 0:
            oos.append(sku)
        elif available < item.quantity:
            qty_fail.append(sku)
        price = effective_price(live)
        live_total += price * item.quantity
        if Decimal(item.unit_price_snapshot) != price:
            price_changed.append(f"{sku}:{item.unit_price_snapshot}->{price}")

    if snapshot_skus and sorted(snapshot_skus) != sorted(live_skus):
        wrong_variant.append("snapshot_mismatch")

    checks = [
        _fail(RevalidationCheckName.SKU_EXISTS, "SKU_NOT_FOUND", detail=", ".join(missing))
        if missing
        else _pass(RevalidationCheckName.SKU_EXISTS, value=",".join(live_skus)),
        _fail(RevalidationCheckName.PRODUCT_ACTIVE, "PRODUCT_INACTIVE", detail=", ".join(inactive_products))
        if inactive_products
        else _pass(RevalidationCheckName.PRODUCT_ACTIVE),
        _fail(RevalidationCheckName.VARIANT_ACTIVE, "VARIANT_INACTIVE", detail=", ".join(inactive_variants))
        if inactive_variants
        else _pass(RevalidationCheckName.VARIANT_ACTIVE),
        _fail(RevalidationCheckName.CORRECT_VARIANT, "VARIANT_MISMATCH", detail=", ".join(wrong_variant))
        if wrong_variant
        else _pass(RevalidationCheckName.CORRECT_VARIANT),
        _fail(RevalidationCheckName.INVENTORY_AVAILABLE, "OUT_OF_STOCK", detail=", ".join(oos), value=oos[0] if oos else None)
        if oos
        else _pass(RevalidationCheckName.INVENTORY_AVAILABLE),
        _fail(RevalidationCheckName.QUANTITY_AVAILABLE, "QUANTITY_UNAVAILABLE", detail=", ".join(qty_fail))
        if qty_fail
        else _pass(RevalidationCheckName.QUANTITY_AVAILABLE)
        if not oos
        else _fail(RevalidationCheckName.QUANTITY_AVAILABLE, "OUT_OF_STOCK", detail=", ".join(oos)),
        _fail(RevalidationCheckName.PRICE_UNCHANGED, "PRICE_CHANGED", detail="; ".join(price_changed))
        if price_changed
        else _pass(RevalidationCheckName.PRICE_UNCHANGED, value=str(live_total)),
    ]
    if oos or qty_fail:
        changed.append("inventory")
    if price_changed:
        changed.append("price")
    if inactive_products or inactive_variants:
        changed.append("catalogue")
    return checks, live_total, changed


def _hard_budget_check(
    intent: ShopperIntent,
    live_total: Decimal | None,
    sku_checks: list[RevalidationCheckResult],
) -> RevalidationCheckResult:
    if intent.budget.type != BudgetType.HARD or intent.budget.amount is None:
        return _na(RevalidationCheckName.HARD_BUDGET, "Budget is not HARD.")
    if live_total is None or any(
        item.status is CheckStatus.FAIL and item.name is RevalidationCheckName.SKU_EXISTS
        for item in sku_checks
    ):
        return _fail(
            RevalidationCheckName.HARD_BUDGET,
            HARD_BUDGET_VIOLATION,
            detail="Cannot recompute total from authoritative prices.",
        )
    if live_total > intent.budget.amount:
        return _fail(
            RevalidationCheckName.HARD_BUDGET,
            HARD_BUDGET_VIOLATION,
            detail=f"{live_total} exceeds HARD budget {intent.budget.amount}",
            value=str(live_total),
        )
    return _pass(RevalidationCheckName.HARD_BUDGET, value=str(live_total))


def _offer_and_policy(
    db: Session,
    shopping: ShoppingSession,
    target: Basket,
    intent: ShopperIntent,
    approval: Approval,
    offer_ref_id: str | None,
) -> tuple[list[RevalidationCheckResult], list[str]]:
    skus = [item.variant.ref_id for item in target.items if item.variant]
    changed: list[str] = []
    checkout = validate_action(
        db,
        shopping,
        ProposedAction(
            ref_id=approval.action_ref_id,
            session_ref_id=shopping.ref_id,
            friction_type=FrictionType.CHECKOUT_HESITATION,
            action=BoundedAction.REQUEST_CHECKOUT,
            reason="Final revalidation of an approved basket.",
            evidence_ref_ids=["EVD-REVAL"],
            candidate_skus=skus,
            confidence=Decimal("1.00"),
            what="Revalidate approved basket",
            why="Approval is not success",
            fix="Stop checkout if commercial state changed",
        ),
        intent=intent,
        persist=True,
    )
    policies = load_merchant_policies(db, shopping.merchant_id)
    _ = policies
    margin = _from_policy(checkout, PolicyCheckName.MARGIN, RevalidationCheckName.MARGIN_VALID, "MARGIN_FLOOR_VIOLATION")
    merchant = (
        _pass(RevalidationCheckName.MERCHANT_POLICY_VALID, value=checkout.ref_id)
        if checkout.decision is not PolicyVerdict.BLOCK
        else _fail(
            RevalidationCheckName.MERCHANT_POLICY_VALID,
            checkout.reason_codes[0] if checkout.reason_codes else "POLICY_BLOCK",
            detail=f"Live policy {checkout.decision.value}",
        )
    )

    if not offer_ref_id:
        offers = [
            _na(RevalidationCheckName.OFFER_EXISTS, "No offer on the approved plan."),
            _na(RevalidationCheckName.OFFER_ACTIVE, "No offer on the approved plan."),
            _na(RevalidationCheckName.OFFER_ELIGIBILITY, "No offer on the approved plan."),
        ]
        return [*offers, margin, merchant], changed

    offer_policy = validate_action(
        db,
        shopping,
        ProposedAction(
            ref_id=approval.action_ref_id,
            session_ref_id=shopping.ref_id,
            friction_type=FrictionType.PRICE_HESITATION,
            action=BoundedAction.APPLY_AUTHORIZED_OFFER,
            reason="Revalidate offer on the approved plan.",
            evidence_ref_ids=["EVD-REVAL"],
            candidate_skus=skus,
            offer_ref_id=offer_ref_id,
            confidence=Decimal("1.00"),
            what="Revalidate authorised offer",
            why="Do not silently drop a discount",
            fix="STOP if the offer is no longer valid",
        ),
        intent=intent,
        persist=True,
    )
    exists = _from_policy(
        offer_policy, PolicyCheckName.AUTHORIZED_OFFER, RevalidationCheckName.OFFER_EXISTS, "UNKNOWN_OFFER"
    )
    active = _from_policy(
        offer_policy, PolicyCheckName.OFFER_ACTIVE, RevalidationCheckName.OFFER_ACTIVE, "OFFER_INACTIVE"
    )
    eligible = _from_policy(
        offer_policy, PolicyCheckName.OFFER_ELIGIBILITY, RevalidationCheckName.OFFER_ELIGIBILITY, "OFFER_NOT_ELIGIBLE"
    )
    stacking = next(
        (item for item in offer_policy.checks if item.name is PolicyCheckName.OFFER_STACKING),
        None,
    )
    if stacking is not None and stacking.status is CheckStatus.FAIL:
        eligible = _fail(
            RevalidationCheckName.OFFER_ELIGIBILITY,
            stacking.reason_code or "OFFER_STACKING_PROHIBITED",
            detail=stacking.detail,
        )
    if offer_policy.decision is PolicyVerdict.BLOCK:
        changed.append("offer")
        if offer_policy.reason_codes:
            merchant = _fail(
                RevalidationCheckName.MERCHANT_POLICY_VALID,
                offer_policy.reason_codes[0],
                detail="Live offer policy blocked.",
            )
    offer_margin = _from_policy(
        offer_policy, PolicyCheckName.MARGIN, RevalidationCheckName.MARGIN_VALID, "MARGIN_FLOOR_VIOLATION"
    )
    if offer_margin.status is CheckStatus.FAIL:
        margin = offer_margin
    return [exists, active, eligible, margin, merchant], changed


def _from_policy(
    decision,
    policy_name: PolicyCheckName,
    reval_name: RevalidationCheckName,
    default_reason: str,
) -> RevalidationCheckResult:
    item = next((check for check in decision.checks if check.name is policy_name), None)
    if item is None:
        return _na(reval_name, "Policy did not emit this check.")
    if item.status is CheckStatus.NA:
        return _na(reval_name, item.detail)
    if item.status is CheckStatus.FAIL:
        return _fail(reval_name, item.reason_code or default_reason, detail=item.detail, value=item.value)
    return _pass(reval_name, detail=item.detail, value=item.value)


def _overall(checks: list[RevalidationCheckResult]) -> tuple[RevalidationStatus, list[str]]:
    fails = [item for item in checks if item.status is CheckStatus.FAIL]
    reasons = [item.reason_code for item in fails if item.reason_code]
    if not fails:
        return RevalidationStatus.PASS, []
    if any(item.name in _STOP_ON for item in fails):
        return RevalidationStatus.STOPPED, reasons
    return RevalidationStatus.FAILED, reasons


def _existing(db: Session, approval_ref_id: str, fingerprint: str) -> RevalidationResultRow | None:
    return db.scalar(
        select(RevalidationResultRow).where(
            RevalidationResultRow.approval_ref_id == approval_ref_id,
            RevalidationResultRow.state_fingerprint == fingerprint,
        )
    )


def _commercial_fingerprint(
    db: Session,
    approval: Approval | None,
    target: Basket | None,
    offer_ref_id: str | None,
    *,
    stale: bool,
    extra: str | None = None,
    intent: ShopperIntent | None = None,
) -> str:
    lines = []
    if target is not None:
        for item in target.items:
            sku = item.variant.ref_id if item.variant is not None else None
            live = get_variant_by_sku(db, sku) if sku else None
            lines.append(
                {
                    "sku": sku,
                    "qty": item.quantity,
                    "snapshot": str(item.unit_price_snapshot),
                    "live": str(effective_price(live)) if live is not None else None,
                    "stock": get_available_quantity(db, sku) if sku else 0,
                    "product_active": bool(live is not None and live.product is not None and live.product.is_active),
                    "variant_active": bool(live is not None and live.is_active),
                }
            )
    offer_state = None
    if offer_ref_id:
        offer = db.scalar(select(Offer).where(Offer.ref_id == offer_ref_id))
        if offer is not None:
            offer_state = {
                "ref": offer.ref_id,
                "active": offer.is_active,
                "starts": str(offer.starts_at),
                "ends": str(offer.ends_at),
            }
        else:
            offer_state = {"ref": offer_ref_id, "active": False, "missing": True}
    payload = {
        "approval": None if approval is None else approval.ref_id,
        "approval_status": None if approval is None else approval.status,
        "basket": version_label(target) if target is not None else None,
        "stale": stale,
        "extra": extra,
        "lines": lines,
        "offer": offer_state,
        "budget": None
        if intent is None
        else {"amount": str(intent.budget.amount), "type": None if intent.budget.type is None else intent.budget.type.value},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _row_to_schema(row: RevalidationResultRow, session_ref_id: str, *, reused: bool) -> RevalidationResult:
    checks = [RevalidationCheckResult.model_validate(item) for item in (row.checks or [])]
    return RevalidationResult(
        ref_id=row.ref_id,
        session_ref_id=session_ref_id,
        basket_ref_id=row.basket_ref_id,
        basket_version=row.basket_version,
        approval_ref_id=row.approval_ref_id,
        status=RevalidationStatus(row.status),
        checks=checks,
        failure_reasons=list(row.failure_reasons or []),
        changed_fields=list(row.changed_fields or []),
        evidence_ref_ids=list(row.evidence_ref_ids or []),
        validated_at=row.validated_at,
        offer_ref_id=row.offer_ref_id,
        reused=reused,
    )


def _finalize(
    db: Session,
    shopping: ShoppingSession,
    *,
    approval_ref_id: str,
    approved: Approval | None,
    target: Basket | None,
    checks: list[RevalidationCheckResult],
    changed: list[str],
    now: datetime,
    persist: bool,
    fingerprint: str,
    offer_ref_id: str | None = None,
    resulting_subtotal: Decimal | None = None,
) -> RevalidationResult:
    status, reasons = _overall(checks)
    unique_changed = list(dict.fromkeys(changed))
    draft = RevalidationResult(
        session_ref_id=shopping.ref_id,
        basket_ref_id=target.ref_id if target is not None else None,
        basket_version=target.version if target is not None else None,
        approval_ref_id=approval_ref_id,
        status=status,
        checks=checks,
        failure_reasons=reasons,
        changed_fields=unique_changed,
        evidence_ref_ids=[],
        validated_at=now,
        offer_ref_id=offer_ref_id,
        resulting_subtotal=resulting_subtotal,
        reused=False,
    )
    if not persist:
        return draft

    existing = _existing(db, approval_ref_id, fingerprint)
    if existing is not None:
        return _row_to_schema(existing, shopping.ref_id, reused=True)

    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.REVALIDATION.value,
        summary=f"Revalidation {status.value} for {approval_ref_id}",
        payload={
            "approval_ref_id": approval_ref_id,
            "basket_ref": version_label(target) if target is not None else None,
            "status": status.value,
            "failure_reasons": reasons,
            "changed_fields": unique_changed,
            "checks": [item.model_dump(mode="json") for item in checks],
            "resulting_subtotal": str(resulting_subtotal) if resulting_subtotal is not None else None,
            "offer_ref_id": offer_ref_id,
        },
    )
    record_audit(
        db,
        session=shopping,
        actor=Actor.SYSTEM.value,
        event_type="revalidation",
        decision=status.value,
        evidence_ref_ids=[evidence.ref_id],
        payload={
            "approval_ref_id": approval_ref_id,
            "basket_ref": version_label(target) if target is not None else None,
            "failure_reasons": reasons,
        },
    )
    _ = approved
    row = RevalidationResultRow(
        ref_id=next_numeric_ref_id(db, RevalidationResultRow, RefPrefix.REVALIDATION),
        session_id=shopping.id,
        basket_id=None if target is None else target.id,
        basket_ref_id=None if target is None else target.ref_id,
        basket_version=None if target is None else target.version,
        approval_ref_id=approval_ref_id,
        status=status.value,
        checks=[item.model_dump(mode="json") for item in checks],
        failure_reasons=reasons,
        changed_fields=unique_changed,
        evidence_ref_ids=[evidence.ref_id],
        state_fingerprint=fingerprint,
        offer_ref_id=offer_ref_id,
        validated_at=now,
    )
    db.add(row)
    db.flush()
    return draft.model_copy(update={"ref_id": row.ref_id, "evidence_ref_ids": [evidence.ref_id]})
