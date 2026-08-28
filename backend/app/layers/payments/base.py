"""PaymentProvider protocol. Stub first; Razorpay Test Mode in M13."""

from typing import Any, Protocol


class PaymentProvider(Protocol):
    def create_order(self, *, frozen_basket: dict[str, Any]) -> dict[str, Any]:
        """Create a payment order from a revalidated, approved basket snapshot."""
        ...

    def verify_webhook(self, *, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify provider signature and return a normalized payment event."""
        ...
