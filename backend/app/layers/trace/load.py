"""Load persisted session graphs for reconstruction. No writes."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.layers.session import SessionServiceError
from app.models import (
    Approval,
    Basket,
    BasketItem,
    Merchant,
    Payment,
    ProductVariant,
    ShoppingSession,
    WebhookEvent,
)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def load_session(db: Session, session_ref_id: str) -> ShoppingSession:
    row = db.scalar(
        select(ShoppingSession)
        .execution_options(populate_existing=True)
        .options(
            selectinload(ShoppingSession.customer),
            selectinload(ShoppingSession.events),
            selectinload(ShoppingSession.intents),
            selectinload(ShoppingSession.baskets)
            .selectinload(Basket.items)
            .selectinload(BasketItem.variant)
            .selectinload(ProductVariant.product),
            selectinload(ShoppingSession.approvals).selectinload(Approval.basket),
            selectinload(ShoppingSession.evidence),
            selectinload(ShoppingSession.audit_events),
            selectinload(ShoppingSession.friction_diagnoses),
            selectinload(ShoppingSession.agent_actions),
            selectinload(ShoppingSession.policy_decisions),
            selectinload(ShoppingSession.revalidations),
            selectinload(ShoppingSession.checkout_attempts),
            selectinload(ShoppingSession.payments).selectinload(Payment.checkout_attempt),
            selectinload(ShoppingSession.webhook_events).selectinload(WebhookEvent.checkout_attempt),
        )
        .where(ShoppingSession.ref_id == session_ref_id)
    )
    if row is None:
        raise SessionServiceError("unknown_session", f"Session {session_ref_id} was not found.")
    return row


def load_merchant(db: Session, shopping: ShoppingSession) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.id == shopping.merchant_id))
    if merchant is None:
        raise SessionServiceError("unknown_merchant", "Session merchant was not found.")
    return merchant
