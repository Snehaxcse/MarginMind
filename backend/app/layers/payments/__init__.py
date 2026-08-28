"""Payment provider interface.

create_order only after policy + revalidation PASS.
Webhooks are signature-verified and idempotent in M10.
The LLM never handles credentials. Application code depends on PaymentProvider,
not on Razorpay SDK or HTTP details.
"""

from app.core.config import get_settings
from app.layers.payments.base import PaymentOrder, PaymentProvider
from app.layers.payments.errors import PaymentProviderError
from app.layers.payments.money import from_minor_units, to_minor_units
from app.layers.payments.razorpay import RazorpayPaymentProvider
from app.layers.payments.stub import StubPaymentProvider


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    name = (settings.payment_provider or "stub").strip().lower()
    if name == "razorpay":
        return RazorpayPaymentProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
        )
    return StubPaymentProvider(key_id=settings.razorpay_key_id or "rzp_test_stub")


__all__ = [
    "PaymentOrder",
    "PaymentProvider",
    "PaymentProviderError",
    "RazorpayPaymentProvider",
    "StubPaymentProvider",
    "from_minor_units",
    "get_payment_provider",
    "to_minor_units",
]
