"""Typed payment-provider failures. Never leak secrets in messages."""


class PaymentProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
