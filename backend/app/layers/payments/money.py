"""Integer minor-unit conversion. Never use floating point for payment amounts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.layers.payments.errors import PaymentProviderError

INR = "INR"
PAISE_QUANTUM = Decimal("0.01")


def to_minor_units(amount: Decimal, *, currency: str = INR) -> int:
    """₹2,447.00 → 244700 paise. Rejects currencies other than INR in M9."""
    if currency != INR:
        raise PaymentProviderError(
            "unsupported_currency",
            f"M9 supports {INR} only; got {currency}.",
        )
    if not isinstance(amount, Decimal):
        raise PaymentProviderError("invalid_amount", "Amount must be a Decimal.")
    quantized = amount.quantize(PAISE_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized < 0:
        raise PaymentProviderError("invalid_amount", "Amount cannot be negative.")
    minor = (quantized * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP)
    return int(minor)


def from_minor_units(amount_minor: int, *, currency: str = INR) -> Decimal:
    if currency != INR:
        raise PaymentProviderError(
            "unsupported_currency",
            f"M9 supports {INR} only; got {currency}.",
        )
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise PaymentProviderError("invalid_amount", "Minor units must be an int.")
    if amount_minor < 0:
        raise PaymentProviderError("invalid_amount", "Amount cannot be negative.")
    return (Decimal(amount_minor) / Decimal(100)).quantize(PAISE_QUANTUM)
