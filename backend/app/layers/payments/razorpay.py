"""Razorpay Test Mode provider. Uses HTTP, not the official SDK.

The application layer must depend on PaymentProvider, not this module.
RAZORPAY_WEBHOOK_SECRET is distinct from RAZORPAY_KEY_SECRET.
Secrets are never logged or returned.
"""

from __future__ import annotations

import json

import httpx

from app.layers.payments.base import PaymentOrder
from app.layers.payments.errors import PaymentProviderError
from app.layers.payments.signature import signatures_match
from app.layers.payments.webhook import PaymentSnapshot, VerifiedWebhookEnvelope, parse_razorpay_event

ORDERS_URL = "https://api.razorpay.com/v1/orders"
PAYMENTS_URL = "https://api.razorpay.com/v1/payments"


class RazorpayPaymentProvider:
    """Live Test Mode orders + raw-body webhook HMAC."""

    name = "razorpay"

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str = "",
        timeout: float = 15.0,
    ) -> None:
        if not key_id or not key_secret:
            raise PaymentProviderError(
                "not_configured",
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required for Razorpay.",
            )
        self.key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self.timeout = timeout

    def create_order(
        self,
        *,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str],
        idempotency_key: str,
    ) -> PaymentOrder:
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise PaymentProviderError("invalid_amount", "amount_minor must be an int.")
        payload = {
            "amount": amount_minor,
            "currency": currency,
            "receipt": receipt[:40],
            "notes": {key: str(value)[:256] for key, value in notes.items()},
        }
        headers = {"X-Razorpay-Idempotency": idempotency_key[:64]}
        try:
            response = httpx.post(
                ORDERS_URL,
                auth=(self.key_id, self._key_secret),
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(
                "provider_failed",
                "Razorpay order request failed.",
            ) from exc
        if response.status_code >= 400:
            raise PaymentProviderError(
                "provider_failed",
                f"Razorpay rejected the order (HTTP {response.status_code}).",
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PaymentProviderError(
                "provider_failed",
                "Razorpay returned a non-JSON order response.",
            ) from exc
        order_id = body.get("id")
        if not order_id:
            raise PaymentProviderError(
                "provider_failed",
                "Razorpay order response was missing an id.",
            )
        return PaymentOrder(
            provider=self.name,
            provider_order_id=str(order_id),
            amount_minor=int(body.get("amount", amount_minor)),
            currency=str(body.get("currency", currency)),
            key_id=self.key_id,
            raw={"id": str(order_id), "receipt": body.get("receipt")},
        )

    def verify_webhook(self, *, payload: bytes, signature: str) -> VerifiedWebhookEnvelope:
        if not self._webhook_secret:
            raise PaymentProviderError(
                "not_configured",
                "RAZORPAY_WEBHOOK_SECRET is required for webhook verification.",
            )
        if not signatures_match(secret=self._webhook_secret, body=payload, signature=signature):
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
        try:
            response = httpx.get(
                f"{PAYMENTS_URL}/{provider_payment_id}",
                auth=(self.key_id, self._key_secret),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(
                "provider_failed",
                "Razorpay payment fetch failed.",
            ) from exc
        if response.status_code >= 400:
            raise PaymentProviderError(
                "provider_failed",
                f"Razorpay payment fetch was rejected (HTTP {response.status_code}).",
            )
        body = response.json()
        return PaymentSnapshot(
            provider=self.name,
            provider_payment_id=str(body.get("id") or provider_payment_id),
            provider_order_id=None if body.get("order_id") is None else str(body.get("order_id")),
            amount_minor=int(body.get("amount") or 0),
            currency=str(body.get("currency") or "INR"),
            status=str(body.get("status") or ""),
        )
