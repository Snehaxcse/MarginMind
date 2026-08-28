"""Checkout service errors. Typed codes; no provider secrets."""

from __future__ import annotations


class CheckoutServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        failure_reasons: list[str] | None = None,
        revalidation_ref_id: str | None = None,
        checkout_attempt_ref: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.failure_reasons = list(failure_reasons or [])
        self.revalidation_ref_id = revalidation_ref_id
        self.checkout_attempt_ref = checkout_attempt_ref
        super().__init__(message)
