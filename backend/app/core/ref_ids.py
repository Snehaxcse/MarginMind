"""Stable human-readable reference IDs for traces, seed data, and evaluation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session


class RefPrefix:
    MERCHANT = "MER"
    CUSTOMER = "CUS"
    PRODUCT = "PRD"
    VARIANT = "SKU"
    SESSION = "SES"
    BASKET = "BASK"
    POLICY = "POL"
    OFFER = "OFR"
    EVIDENCE = "EVD"
    AUDIT = "AUD"
    APPROVAL = "APR"
    INTENT = "INT"
    EVENT = "EVT"
    PREFERENCE = "PREF"
    FRICTION = "FRIC"
    ACTION = "ACT"
    POLICY_DECISION = "PDEC"
    REVALIDATION = "REVAL"
    CHECKOUT = "CHK"
    PAYMENT = "PAY"


def format_ref_id(prefix: str, n: int, *, suffix: str | None = None) -> str:
    body = f"{prefix}-{n:03d}"
    if suffix:
        return f"{body}-{suffix}"
    return body


def basket_version_ref(basket_ref_id: str, version: int) -> str:
    return f"{basket_ref_id}@v{version}"


def parse_basket_version_ref(value: str) -> tuple[str, int | None]:
    if "@v" not in value:
        return value, None
    base, _, rest = value.partition("@v")
    if not rest.isdigit():
        raise ValueError(f"Invalid basket version ref: {value}")
    return base, int(rest)



def _numeric_suffix(ref_id: str, prefix: str) -> int | None:
    if not ref_id.startswith(f"{prefix}-"):
        return None
    rest = ref_id[len(prefix) + 1 :]
    head = rest.split("-", 1)[0]
    if head.isdigit():
        return int(head)
    return None


def next_numeric_ref_id(session: Session, model: type, prefix: str) -> str:
    """Allocate the next PREFIX-NNN for a model that has ref_id.

    Seed data uses explicit IDs. Call this for runtime-created rows.
    """
    refs = session.scalars(select(model.ref_id).where(model.ref_id.like(f"{prefix}-%"))).all()
    max_n = 0
    for ref in refs:
        n = _numeric_suffix(ref, prefix)
        if n is not None:
            max_n = max(max_n, n)
    return format_ref_id(prefix, max_n + 1)
