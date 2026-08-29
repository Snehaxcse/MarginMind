"""PaymentProvider protocol. Application code depends on this, not on Razorpay."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.layers.payments.webhook import PaymentSnapshot, VerifiedWebhookEnvelope


class PaymentOrder(BaseModel):
    """Provider order created from an already-revalidated, approved basket."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_order_id: str
    amount_minor: int
    currency: str
    key_id: str | None = None
    raw: dict = Field(default_factory=dict)


class PaymentProvider(Protocol):
    """create_order only after policy + revalidation PASS.

    verify_webhook checks HMAC of the raw body. It does not mark payment VERIFIED.
    """

    name: str

    def create_order(
        self,
        *,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str],
        idempotency_key: str,
    ) -> PaymentOrder:
        """Create a payment order. Amount is integer minor units from server truth."""
        ...

    def verify_webhook(self, *, payload: bytes, signature: str) -> VerifiedWebhookEnvelope:
        """Verify raw-body HMAC and return a typed envelope. Parse JSON only after verify."""
        ...

    def fetch_payment(self, provider_payment_id: str) -> PaymentSnapshot:
        """Optional independent fetch. Automated tests use the stub, never live Razorpay."""
        ...
