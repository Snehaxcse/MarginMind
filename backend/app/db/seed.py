"""Idempotent development seed. Running twice must not duplicate rows."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import seed_data as data
from app.db.session import session_scope
from app.models import (
    Customer,
    CustomerPreference,
    Inventory,
    Merchant,
    MerchantPolicy,
    Offer,
    Product,
    ProductVariant,
)

TRUNCATE_SQL = """
TRUNCATE TABLE
    payments,
    checkout_attempts,
    revalidation_results,
    policy_decisions,
    agent_actions,
    friction_diagnoses,
    audit_events,
    evidence,
    approvals,
    basket_items,
    baskets,
    intents,
    session_events,
    shopping_sessions,
    customer_preferences,
    customers,
    inventory,
    product_variants,
    products,
    offers,
    merchant_policies,
    merchants
RESTART IDENTITY CASCADE
"""


def _upsert_by_ref(session: Session, model: type, ref_id: str, **fields):
    row = session.scalar(select(model).where(model.ref_id == ref_id))
    if row is None:
        row = model(ref_id=ref_id, **fields)
        session.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    session.flush()
    return row


def seed_merchant(session: Session) -> Merchant:
    return _upsert_by_ref(session, Merchant, **data.MERCHANT)


def seed_policies(session: Session, merchant: Merchant) -> None:
    for policy in data.POLICIES:
        _upsert_by_ref(session, MerchantPolicy, merchant_id=merchant.id, **policy)


def seed_offers(session: Session, merchant: Merchant) -> None:
    for offer in data.OFFERS:
        _upsert_by_ref(session, Offer, merchant_id=merchant.id, **offer)


def seed_customer(session: Session, merchant: Merchant) -> Customer:
    customer = _upsert_by_ref(session, Customer, merchant_id=merchant.id, **data.CUSTOMER)
    for pref in data.CUSTOMER_PREFERENCES:
        existing = session.scalar(
            select(CustomerPreference).where(CustomerPreference.ref_id == pref["ref_id"])
        )
        if existing is None:
            session.add(CustomerPreference(customer_id=customer.id, **pref))
        else:
            existing.customer_id = customer.id
            existing.key = pref["key"]
            existing.value = pref["value"]
            existing.kind = pref["kind"]
    session.flush()
    return customer


def seed_catalogue(session: Session, merchant: Merchant) -> None:
    for product_data in data.PRODUCTS:
        variants = product_data["variants"]
        product_fields = {k: v for k, v in product_data.items() if k != "variants"}
        product = _upsert_by_ref(session, Product, merchant_id=merchant.id, **product_fields)
        for variant_data in variants:
            stock = variant_data["stock"]
            variant_fields = {
                "product_id": product.id,
                "size": variant_data["size"],
                "colour": variant_data["colour"],
                "price_override": variant_data["price_override"],
                "is_active": True,
            }
            variant = _upsert_by_ref(
                session, ProductVariant, variant_data["ref_id"], **variant_fields
            )
            inventory = session.scalar(
                select(Inventory).where(Inventory.variant_id == variant.id)
            )
            if inventory is None:
                session.add(
                    Inventory(variant_id=variant.id, quantity=stock, reserved_quantity=0)
                )
            else:
                inventory.quantity = stock
                inventory.reserved_quantity = 0
    session.flush()


def seed_all(session: Session) -> None:
    merchant = seed_merchant(session)
    seed_policies(session, merchant)
    seed_offers(session, merchant)
    seed_customer(session, merchant)
    seed_catalogue(session, merchant)


def reset_all(session: Session) -> None:
    session.execute(text(TRUNCATE_SQL))
    session.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed MarginMind development data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate all MVP tables then seed. Destroys local development data.",
    )
    args = parser.parse_args(argv)
    with session_scope() as session:
        if args.reset:
            reset_all(session)
        seed_all(session)
    print("Seed complete (idempotent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
