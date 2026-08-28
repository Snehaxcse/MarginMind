"""Customer approval bound to an exact session, action, and basket version.

Granting approval does not execute the action. BASK-001@v1 never covers @v2.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ref_ids import RefPrefix, basket_version_ref, next_numeric_ref_id
from app.layers.basket import version_label
from app.models import Approval, Basket, ShoppingSession
from app.schemas.vocabulary import ApprovalStatus, CheckoutState


class ApprovalServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def create_approval_request(
    db: Session,
    shopping: ShoppingSession,
    basket: Basket,
    *,
    action_ref_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> Approval:
    if basket.session_id != shopping.id:
        raise ApprovalServiceError("session_mismatch", "Basket does not belong to this session.")
    payload = {
        "basket_ref": version_label(basket),
        "action_ref_id": action_ref_id,
        "skus": [item.variant.ref_id for item in basket.items if item.variant],
        **(snapshot or {}),
    }
    row = Approval(
        ref_id=next_numeric_ref_id(db, Approval, RefPrefix.APPROVAL),
        session_id=shopping.id,
        basket_id=basket.id,
        customer_id=shopping.customer_id,
        basket_version=basket.version,
        action_ref_id=action_ref_id,
        status=ApprovalStatus.PENDING.value,
        snapshot=payload,
    )
    db.add(row)
    db.flush()
    return row


def get_approval(db: Session, ref_id: str) -> Approval | None:
    return db.scalar(select(Approval).where(Approval.ref_id == ref_id))


def require_approval(db: Session, ref_id: str) -> Approval:
    row = get_approval(db, ref_id)
    if row is None:
        raise ApprovalServiceError("unknown_approval", f"Approval {ref_id} was not found.")
    return row


def approve(db: Session, ref_id: str) -> Approval:
    """Mark pending approval granted. Does not mutate baskets, offers, or inventory."""
    row = require_approval(db, ref_id)
    if row.status != ApprovalStatus.PENDING.value:
        raise ApprovalServiceError(
            "not_pending",
            f"Approval {ref_id} is {row.status}, not pending.",
        )
    row.status = ApprovalStatus.GRANTED.value
    if row.basket is not None and row.basket.status == CheckoutState.DRAFT_BASKET.value:
        row.basket.status = CheckoutState.APPROVED_UNVERIFIED.value
    db.flush()
    return row


def reject(db: Session, ref_id: str) -> Approval:
    row = require_approval(db, ref_id)
    if row.status != ApprovalStatus.PENDING.value:
        raise ApprovalServiceError(
            "not_pending",
            f"Approval {ref_id} is {row.status}, not pending.",
        )
    row.status = ApprovalStatus.REJECTED.value
    db.flush()
    return row


def approval_covers(
    db: Session,
    shopping: ShoppingSession,
    *,
    action_ref_id: str | None = None,
    basket: Basket | None = None,
    basket_ref_id: str | None = None,
    basket_version: int | None = None,
) -> Approval | None:
    """Return the granted approval that binds this exact session/action/version, if any."""
    stmt = select(Approval).where(
        Approval.session_id == shopping.id,
        Approval.status == ApprovalStatus.GRANTED.value,
    )
    if action_ref_id is not None:
        stmt = stmt.where(Approval.action_ref_id == action_ref_id)
    if basket is not None:
        stmt = stmt.where(
            Approval.basket_id == basket.id,
            Approval.basket_version == basket.version,
        )
    else:
        if basket_ref_id is not None:
            stmt = stmt.where(Approval.basket.has(Basket.ref_id == basket_ref_id))
        if basket_version is not None:
            stmt = stmt.where(Approval.basket_version == basket_version)
    return db.scalar(stmt.order_by(Approval.created_at.desc()))


def version_approval_covers(
    db: Session,
    shopping: ShoppingSession,
    basket: Basket,
    *,
    action_ref_id: str | None = None,
) -> bool:
    """True only when a granted approval matches this basket row and version.

    BASK-001@v1 never authorizes BASK-001@v2. Optional action_ref_id must match
    when the approval was bound to an action.
    """
    _ = basket_version_ref
    stmt = select(Approval).where(
        Approval.session_id == shopping.id,
        Approval.status == ApprovalStatus.GRANTED.value,
        Approval.basket_id == basket.id,
        Approval.basket_version == basket.version,
    )
    if action_ref_id is not None:
        stmt = stmt.where(Approval.action_ref_id == action_ref_id)
    return db.scalar(stmt) is not None
