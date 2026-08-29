"""Deterministic PaymentProvider for tests. Never calls the network."""

from __future__ import annotations

import json

from app.layers.payments.base import PaymentOrder
from app.layers.payments.errors import PaymentProviderError
from app.layers.payments.signature import hmac_sha256_hex, signatures_match
from app.layers.payments.webhook import PaymentSnapshot, VerifiedWebhookEnvelope, parse_razorpay_event

STUB_WEBHOOK_SECRET = "stub_webhook_secret"


class StubPaymentProvider:
    """Fake Razorpay-shaped orders and signed webhook fixtures."""

    name = "stub"

    def __init__(
        self,
        *,
        fail: bool = False,
        key_id: str = "rzp_test_stub",
        webhook_secret: str = STUB_WEBHOOK_SECRET,
        key_secret: str = "stub_key_secret_not_for_webhooks",
    ) -> None:
        self.fail = fail
        self.key_id = key_id
        self.webhook_secret = webhook_secret
        self.key_secret = key_secret
        self.created: list[PaymentOrder] = []
        self.payments: dict[str, PaymentSnapshot] = {}

    def sign_webhook(self, body: bytes) -> str:
        """HMAC-SHA256 hex of the exact raw bytes. Uses webhook secret, not key secret."""
        return hmac_sha256_hex(secret=self.webhook_secret, body=body)

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

    def verify_webhook(self, *, payload: bytes, signature: str) -> VerifiedWebhookEnvelope:
        if not signatures_match(secret=self.webhook_secret, body=payload, signature=signature):
            raise PaymentProviderError("invalid_signature", "Webhook signature is invalid.")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentProviderError(
                "invalid_payload",
                "Webhook body is not JSON.",
            ) from exc
        if not isinstance(data, dict):
            raise PaymentProviderError("invalid_payload", "Webhook JSON must be an object.")
        return parse_razorpay_event(data, provider=self.name)

    def fetch_payment(self, provider_payment_id: str) -> PaymentSnapshot:
        snapshot = self.payments.get(provider_payment_id)
        if snapshot is None:
            raise PaymentProviderError("unknown_payment", f"Payment {provider_payment_id} was not found.")
        return snapshot
