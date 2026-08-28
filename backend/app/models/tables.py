"""MVP commercial-truth tables. Inventory, price, and SKU live here — not in the LLM."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customers: Mapped[list[Customer]] = relationship(back_populates="merchant")
    products: Mapped[list[Product]] = relationship(back_populates="merchant")
    policies: Mapped[list[MerchantPolicy]] = relationship(back_populates="merchant")
    offers: Mapped[list[Offer]] = relationship(back_populates="merchant")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    merchant: Mapped[Merchant] = relationship(back_populates="customers")
    preferences: Mapped[list[CustomerPreference]] = relationship(back_populates="customer")
    sessions: Mapped[list[ShoppingSession]] = relationship(back_populates="customer")


class CustomerPreference(Base):
    __tablename__ = "customer_preferences"
    __table_args__ = (UniqueConstraint("customer_id", "key", "value", name="uq_customer_pref"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # HARD | SOFT
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="preferences")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    colour: Mapped[str] = mapped_column(String(64))
    material: Mapped[str] = mapped_column(String(64))
    fit: Mapped[str] = mapped_column(String(64))
    silhouette: Mapped[str] = mapped_column(String(64))
    length: Mapped[str] = mapped_column(String(64))
    stretch: Mapped[str] = mapped_column(String(32))
    coverage: Mapped[str] = mapped_column(String(64))
    occasion_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    style_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    margin_band: Mapped[str] = mapped_column(String(16))
    margin_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    merchant: Mapped[Merchant] = relationship(back_populates="products")
    variants: Mapped[list[ProductVariant]] = relationship(back_populates="product")


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # SKU
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    size: Mapped[str] = mapped_column(String(16), index=True)
    colour: Mapped[str] = mapped_column(String(64))
    price_override: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    product: Mapped[Product] = relationship(back_populates="variants")
    inventory: Mapped[Inventory | None] = relationship(back_populates="variant", uselist=False)


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id"), unique=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    variant: Mapped[ProductVariant] = relationship(back_populates="inventory")


class MerchantPolicy(Base):
    __tablename__ = "merchant_policies"
    __table_args__ = (UniqueConstraint("merchant_id", "code", name="uq_merchant_policy_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    value_type: Mapped[str] = mapped_column(String(16))
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    merchant: Mapped[Merchant] = relationship(back_populates="policies")


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    discount_type: Mapped[str] = mapped_column(String(16))
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_basket_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    max_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    eligible_categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    eligible_product_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stackable: Mapped[bool] = mapped_column(Boolean, default=False)
    min_margin_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    merchant: Mapped[Merchant] = relationship(back_populates="offers")


class ShoppingSession(Base):
    __tablename__ = "shopping_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped[Customer] = relationship(back_populates="sessions")
    events: Mapped[list[SessionEvent]] = relationship(back_populates="session")
    intents: Mapped[list[Intent]] = relationship(back_populates="session")
    baskets: Mapped[list[Basket]] = relationship(back_populates="session")
    approvals: Mapped[list[Approval]] = relationship(back_populates="session")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="session")
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="session")
    friction_diagnoses: Mapped[list[FrictionDiagnosis]] = relationship(
        back_populates="session"
    )
    agent_actions: Mapped[list[AgentAction]] = relationship(back_populates="session")
    policy_decisions: Mapped[list[PolicyDecision]] = relationship(back_populates="session")
    revalidations: Mapped[list[RevalidationResult]] = relationship(back_populates="session")


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="events")


class Intent(Base):
    __tablename__ = "intents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    occasion: Mapped[str | None] = mapped_column(String(64), nullable=True)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    budget_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    height: Mapped[str | None] = mapped_column(String(32), nullable=True)
    usual_size: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fit_preferences: Mapped[list[str]] = mapped_column(JSONB, default=list)
    style_preferences: Mapped[list[str]] = mapped_column(JSONB, default=list)
    colour_preferences: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_materials: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_coverage: Mapped[list[str]] = mapped_column(JSONB, default=list)
    goal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ambiguities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="intents")
    customer: Mapped[Customer | None] = relationship()


class Basket(Base):
    __tablename__ = "baskets"
    __table_args__ = (UniqueConstraint("ref_id", "version", name="uq_basket_ref_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT_BASKET")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    session: Mapped[ShoppingSession] = relationship(back_populates="baskets")
    items: Mapped[list[BasketItem]] = relationship(back_populates="basket")
    approvals: Mapped[list[Approval]] = relationship(back_populates="basket")


class BasketItem(Base):
    __tablename__ = "basket_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    basket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("baskets.id"), index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    basket: Mapped[Basket] = relationship(back_populates="items")
    variant: Mapped[ProductVariant] = relationship()


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    basket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("baskets.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    basket_version: Mapped[int] = mapped_column(Integer)
    action_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="approvals")
    basket: Mapped[Basket] = relationship(back_populates="approvals")
    customer: Mapped[Customer] = relationship()


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shopping_sessions.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession | None] = relationship(back_populates="evidence")


class FrictionDiagnosis(Base):
    __tablename__ = "friction_diagnoses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    friction_type: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="friction_diagnoses")


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    friction_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    candidate_skus: Mapped[list[str]] = mapped_column(JSONB, default=list)
    offer_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    requires_policy_check: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_customer_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    potential_revenue_not_pursued: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    what: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str] = mapped_column(Text, default="")
    fix: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="agent_actions")


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    action_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_customer_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_merchant_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="policy_decisions")


class RevalidationResult(Base):
    __tablename__ = "revalidation_results"
    __table_args__ = (
        UniqueConstraint(
            "approval_ref_id",
            "state_fingerprint",
            name="uq_revalidation_approval_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"), index=True)
    basket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("baskets.id"), nullable=True, index=True
    )
    basket_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    basket_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_ref_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    failure_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    state_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    offer_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession] = relationship(back_populates="revalidations")
    basket: Mapped[Basket | None] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shopping_sessions.id"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_ref_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ShoppingSession | None] = relationship(back_populates="audit_events")
