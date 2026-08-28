"""Checkout attempt payloads. Amounts are integer minor units, never floats."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import CheckoutAttemptStatus, PaymentStatus


class CreateCheckoutRequest(BaseModel):
    """HTTP input. Session + granted approval only. Price fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    session_ref_id: str
    approval_ref_id: str
    basket_ref_id: str | None = None
    amount: Decimal | None = None
    amount_minor: int | None = None
    price: Decimal | None = None
    discounted_total: Decimal | None = None


class CheckoutPayload(BaseModel):
    """Safe client payload for a future Checkout.js widget. No secrets."""

    model_config = ConfigDict(extra="forbid")

    checkout_attempt_ref: str
    session_ref_id: str
    basket_ref_id: str
    basket_version: int
    approval_ref_id: str
    revalidation_ref_id: str | None = None
    provider: str
    provider_order_id: str | None = None
    amount_minor: int
    currency: str
    key_id: str | None = None
    merchant_name: str | None = None
    status: CheckoutAttemptStatus
    reused: bool = False


class CheckoutAttemptView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    session_ref_id: str
    basket_ref_id: str
    basket_version: int
    approval_ref_id: str
    revalidation_ref_id: str | None = None
    amount_minor: int
    currency: str
    provider: str
    provider_order_id: str | None = None
    status: CheckoutAttemptStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    payment_ref_id: str | None = None
    payment_status: PaymentStatus | None = None
    payment_verified_at: datetime | None = None


class ClientPaymentResultRequest(BaseModel):
    """Browser/client Razorpay callback. Never treated as verified payment."""

    model_config = ConfigDict(extra="forbid")

    provider_payment_id: str | None = None
    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None
    razorpay_signature: str | None = None
    client_status: str | None = Field(
        default=None,
        description="Ignored for commercial truth. VERIFIED from the client is not accepted.",
    )
