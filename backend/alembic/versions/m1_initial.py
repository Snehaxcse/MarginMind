"""M1 initial commercial-truth schema.

Revision ID: m1_initial
Revises:
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m1_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_merchants_ref_id", "merchants", ["ref_id"], unique=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customers_ref_id", "customers", ["ref_id"], unique=True)
    op.create_index("ix_customers_merchant_id", "customers", ["merchant_id"])

    op.create_table(
        "customer_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "key", "value", name="uq_customer_pref"),
    )
    op.create_index("ix_customer_preferences_ref_id", "customer_preferences", ["ref_id"], unique=True)
    op.create_index("ix_customer_preferences_customer_id", "customer_preferences", ["customer_id"])
    op.create_index("ix_customer_preferences_key", "customer_preferences", ["key"])

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("colour", sa.String(length=64), nullable=False),
        sa.Column("material", sa.String(length=64), nullable=False),
        sa.Column("fit", sa.String(length=64), nullable=False),
        sa.Column("silhouette", sa.String(length=64), nullable=False),
        sa.Column("length", sa.String(length=64), nullable=False),
        sa.Column("stretch", sa.String(length=32), nullable=False),
        sa.Column("coverage", sa.String(length=64), nullable=False),
        sa.Column("occasion_tags", JSONB, nullable=False),
        sa.Column("style_tags", JSONB, nullable=False),
        sa.Column("margin_band", sa.String(length=16), nullable=False),
        sa.Column("margin_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_ref_id", "products", ["ref_id"], unique=True)
    op.create_index("ix_products_merchant_id", "products", ["merchant_id"])
    op.create_index("ix_products_category", "products", ["category"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("size", sa.String(length=16), nullable=False),
        sa.Column("colour", sa.String(length=64), nullable=False),
        sa.Column("price_override", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_variants_ref_id", "product_variants", ["ref_id"], unique=True)
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])
    op.create_index("ix_product_variants_size", "product_variants", ["size"])

    op.create_table(
        "inventory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_variant_id", "inventory", ["variant_id"], unique=True)

    op.create_table(
        "merchant_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_numeric", sa.Numeric(10, 2), nullable=True),
        sa.Column("value_text", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "code", name="uq_merchant_policy_code"),
    )
    op.create_index("ix_merchant_policies_ref_id", "merchant_policies", ["ref_id"], unique=True)
    op.create_index("ix_merchant_policies_merchant_id", "merchant_policies", ["merchant_id"])
    op.create_index("ix_merchant_policies_code", "merchant_policies", ["code"])

    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("discount_type", sa.String(length=16), nullable=False),
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("min_basket_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("max_discount_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("eligible_categories", JSONB, nullable=False),
        sa.Column("eligible_product_ref_ids", JSONB, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stackable", sa.Boolean(), nullable=False),
        sa.Column("min_margin_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offers_ref_id", "offers", ["ref_id"], unique=True)
    op.create_index("ix_offers_merchant_id", "offers", ["merchant_id"])

    op.create_table(
        "shopping_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shopping_sessions_ref_id", "shopping_sessions", ["ref_id"], unique=True)
    op.create_index("ix_shopping_sessions_merchant_id", "shopping_sessions", ["merchant_id"])
    op.create_index("ix_shopping_sessions_customer_id", "shopping_sessions", ["customer_id"])

    op.create_table(
        "session_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_events_ref_id", "session_events", ["ref_id"], unique=True)
    op.create_index("ix_session_events_session_id", "session_events", ["session_id"])
    op.create_index("ix_session_events_event_type", "session_events", ["event_type"])

    op.create_table(
        "intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("occasion", sa.String(length=64), nullable=True),
        sa.Column("budget_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("budget_type", sa.String(length=16), nullable=True),
        sa.Column("height", sa.String(length=32), nullable=True),
        sa.Column("fit_preferences", JSONB, nullable=False),
        sa.Column("style_preferences", JSONB, nullable=False),
        sa.Column("goal", sa.String(length=64), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_intents_ref_id", "intents", ["ref_id"], unique=True)
    op.create_index("ix_intents_session_id", "intents", ["session_id"])

    op.create_table(
        "baskets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ref_id", "version", name="uq_basket_ref_version"),
    )
    op.create_index("ix_baskets_ref_id", "baskets", ["ref_id"])
    op.create_index("ix_baskets_session_id", "baskets", ["session_id"])

    op.create_table(
        "basket_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("basket_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_snapshot", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["basket_id"], ["baskets.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_basket_items_basket_id", "basket_items", ["basket_id"])
    op.create_index("ix_basket_items_variant_id", "basket_items", ["variant_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("basket_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("basket_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.ForeignKeyConstraint(["basket_id"], ["baskets.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_ref_id", "approvals", ["ref_id"], unique=True)
    op.create_index("ix_approvals_session_id", "approvals", ["session_id"])
    op.create_index("ix_approvals_basket_id", "approvals", ["basket_id"])
    op.create_index("ix_approvals_customer_id", "approvals", ["customer_id"])

    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_ref_id", "evidence", ["ref_id"], unique=True)
    op.create_index("ix_evidence_session_id", "evidence", ["session_id"])
    op.create_index("ix_evidence_kind", "evidence", ["kind"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=True),
        sa.Column("evidence_ref_ids", JSONB, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_ref_id", "audit_events", ["ref_id"], unique=True)
    op.create_index("ix_audit_events_session_id", "audit_events", ["session_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("evidence")
    op.drop_table("approvals")
    op.drop_table("basket_items")
    op.drop_table("baskets")
    op.drop_table("intents")
    op.drop_table("session_events")
    op.drop_table("shopping_sessions")
    op.drop_table("offers")
    op.drop_table("merchant_policies")
    op.drop_table("inventory")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("customer_preferences")
    op.drop_table("customers")
    op.drop_table("merchants")
