"""PaymentProvider protocol. Application code depends on this, not on Razorpay."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class PaymentOrder(BaseModel):
    """Provider order created from an already-revalidated, approved basket."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_order_id: str
    amount_minor: int
    currency: str
    key_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PaymentProvider(Protocol):
    """create_order only after policy + revalidation PASS.

    verify_webhook is M10. M9 must not treat client success as verified payment.
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

    def verify_webhook(self, *, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify provider signature and return a normalized payment event (M10)."""
        ...
