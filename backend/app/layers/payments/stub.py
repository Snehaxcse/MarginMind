"""Deterministic PaymentProvider for tests. Never calls the network."""

from __future__ import annotations

from typing import Any

from app.layers.payments.base import PaymentOrder
from app.layers.payments.errors import PaymentProviderError


class StubPaymentProvider:
    """Fake Razorpay-shaped orders. `fail=True` simulates provider outage."""

    name = "stub"

    def __init__(self, *, fail: bool = False, key_id: str = "rzp_test_stub") -> None:
        self.fail = fail
        self.key_id = key_id
        self.created: list[PaymentOrder] = []

    def create_order(
        self,
        *,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str],
        idempotency_key: str,
    ) -> PaymentOrder:
        if self.fail:
            raise PaymentProviderError(
                "provider_failed",
                "Stub payment provider simulated failure.",
            )
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise PaymentProviderError("invalid_amount", "amount_minor must be an int.")
        order = PaymentOrder(
            provider=self.name,
            provider_order_id=f"order_stub_{receipt}",
            amount_minor=amount_minor,
            currency=currency,
            key_id=self.key_id,
            raw={
                "receipt": receipt,
                "notes": dict(notes),
                "idempotency_key": idempotency_key,
            },
        )
        self.created.append(order)
        return order

    def verify_webhook(self, *, payload: bytes, signature: str) -> dict[str, Any]:
        raise PaymentProviderError(
            "not_implemented",
            "Webhook/signature verification is M10.",
        )
