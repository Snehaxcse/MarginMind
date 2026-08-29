"""Normalized webhook envelope. Parse only after HMAC verification."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CommercialEffect = Literal["none", "authorized", "captured", "failed"]


class PaymentSnapshot(BaseModel):
    """Provider payment fetched independently of webhooks."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_payment_id: str
    provider_order_id: str | None = None
    amount_minor: int
    currency: str
    status: str


class VerifiedWebhookEnvelope(BaseModel):
    """Typed result of a signature-valid webhook. Not yet commercial truth."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    event_type: str
    provider_event_id: str | None = None
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    provider_status: str | None = None
    commercial_effect: CommercialEffect = "none"
    payload_meta: dict[str, Any] = Field(default_factory=dict)


SUPPORTED_WEBHOOK_EVENTS = frozenset(
    {
        "payment.authorized",
        "payment.captured",
        "payment.failed",
        "order.paid",
    }
)


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    block = payload.get(name)
    if not isinstance(block, dict):
        return {}
    entity = block.get("entity")
    return entity if isinstance(entity, dict) else {}


def _int_amount(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_razorpay_event(data: dict[str, Any], *, provider: str) -> VerifiedWebhookEnvelope:
    """Map a Razorpay-shaped JSON object into a provider-neutral envelope."""
    event_type = str(data.get("event") or "")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    payment = _entity(payload, "payment")
    order = _entity(payload, "order")
    payment_id = payment.get("id")
    order_id = payment.get("order_id") or order.get("id")
    amount = _int_amount(payment.get("amount"))
    if amount is None:
        amount = _int_amount(order.get("amount"))
    currency = payment.get("currency") or order.get("currency")
    status = payment.get("status") or order.get("status")
    if event_type == "payment.captured":
        effect: CommercialEffect = "captured"
    elif event_type == "order.paid":
        effect = "captured"
    elif event_type == "payment.authorized":
        effect = "authorized"
    elif event_type == "payment.failed":
        effect = "failed"
    else:
        effect = "none"
    return VerifiedWebhookEnvelope(
        provider=provider,
        event_type=event_type,
        provider_order_id=None if order_id is None else str(order_id),
        provider_payment_id=None if payment_id is None else str(payment_id),
        amount_minor=amount,
        currency=None if currency is None else str(currency),
        provider_status=None if status is None else str(status),
        commercial_effect=effect,
        payload_meta={
            "event": event_type,
            "provider_status": status,
            "contains_payment": bool(payment),
            "contains_order": bool(order),
        },
    )


def encode_razorpay_event(
    *,
    event: str,
    order_id: str,
    payment_id: str,
    amount_minor: int,
    currency: str = "INR",
    status: str = "captured",
) -> bytes:
    """Stable Razorpay-shaped JSON bytes for stub fixtures. Callers HMAC this exact body."""
    payload: dict[str, Any] = {
        "entity": "event",
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_minor,
                    "currency": currency,
                    "status": status,
                    "order_id": order_id,
                }
            }
        },
    }
    if event == "order.paid":
        payload["payload"]["order"] = {
            "entity": {
                "id": order_id,
                "entity": "order",
                "amount": amount_minor,
                "currency": currency,
                "status": "paid",
            }
        }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

