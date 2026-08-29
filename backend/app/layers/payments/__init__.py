"""Payment provider interface.

create_order only after policy + revalidation PASS.
Webhooks are HMAC-verified against the raw body, then applied idempotently.
The LLM never handles credentials. Application code depends on PaymentProvider,
not on Razorpay SDK or HTTP details.
RAZORPAY_WEBHOOK_SECRET is never RAZORPAY_KEY_SECRET.
"""

from app.core.config import get_settings
from app.layers.payments.base import PaymentOrder, PaymentProvider
from app.layers.payments.errors import PaymentProviderError
from app.layers.payments.money import from_minor_units, to_minor_units
from app.layers.payments.razorpay import RazorpayPaymentProvider
from app.layers.payments.signature import hmac_sha256_hex, sha256_hex, signatures_match
from app.layers.payments.stub import STUB_WEBHOOK_SECRET, StubPaymentProvider
from app.layers.payments.webhook import (
    PaymentSnapshot,
    VerifiedWebhookEnvelope,
    encode_razorpay_event,
    parse_razorpay_event,
)


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    name = (settings.payment_provider or "stub").strip().lower()
    if name == "razorpay":
        return RazorpayPaymentProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
        )
    return StubPaymentProvider(
        key_id=settings.razorpay_key_id or "rzp_test_stub",
        webhook_secret=settings.razorpay_webhook_secret or STUB_WEBHOOK_SECRET,
    )


__all__ = [
    "PaymentOrder",
    "PaymentProvider",
    "PaymentProviderError",
    "PaymentSnapshot",
    "RazorpayPaymentProvider",
    "STUB_WEBHOOK_SECRET",
    "StubPaymentProvider",
    "VerifiedWebhookEnvelope",
    "encode_razorpay_event",
    "from_minor_units",
    "get_payment_provider",
    "hmac_sha256_hex",
    "parse_razorpay_event",
    "sha256_hex",
    "signatures_match",
    "to_minor_units",
]
