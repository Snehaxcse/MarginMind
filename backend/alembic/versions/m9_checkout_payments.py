"""M9 checkout_attempts + payments. Client success is not verified payment.

Revision ID: m9_checkout_payments
Revises: m8_revalidation
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m9_checkout_payments"
down_revision: Union[str, None] = "m8_revalidation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "checkout_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("basket_id", sa.Uuid(), nullable=False),
        sa.Column("basket_ref_id", sa.String(length=32), nullable=False),
        sa.Column("basket_version", sa.Integer(), nullable=False),
        sa.Column("approval_ref_id", sa.String(length=32), nullable=False),
        sa.Column("revalidation_ref_id", sa.String(length=32), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.ForeignKeyConstraint(["basket_id"], ["baskets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checkout_attempts_ref_id", "checkout_attempts", ["ref_id"], unique=True)
    op.create_index("ix_checkout_attempts_session_id", "checkout_attempts", ["session_id"])
    op.create_index("ix_checkout_attempts_basket_id", "checkout_attempts", ["basket_id"])
    op.create_index("ix_checkout_attempts_basket_ref_id", "checkout_attempts", ["basket_ref_id"])
    op.create_index("ix_checkout_attempts_approval_ref_id", "checkout_attempts", ["approval_ref_id"])
    op.create_index(
        "ix_checkout_attempts_revalidation_ref_id",
        "checkout_attempts",
        ["revalidation_ref_id"],
    )
    op.create_index(
        "ix_checkout_attempts_provider_order_id",
        "checkout_attempts",
        ["provider_order_id"],
    )
    op.create_index("ix_checkout_attempts_status", "checkout_attempts", ["status"])
    op.create_index(
        "ix_checkout_attempts_idempotency_key",
        "checkout_attempts",
        ["idempotency_key"],
        unique=True,
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("checkout_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.ForeignKeyConstraint(["checkout_attempt_id"], ["checkout_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payments_ref_id", "payments", ["ref_id"], unique=True)
    op.create_index("ix_payments_session_id", "payments", ["session_id"])
    op.create_index("ix_payments_checkout_attempt_id", "payments", ["checkout_attempt_id"], unique=True)
    op.create_index("ix_payments_provider_order_id", "payments", ["provider_order_id"])
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"])
    op.create_index("ix_payments_status", "payments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_provider_payment_id", table_name="payments")
    op.drop_index("ix_payments_provider_order_id", table_name="payments")
    op.drop_index("ix_payments_checkout_attempt_id", table_name="payments")
    op.drop_index("ix_payments_session_id", table_name="payments")
    op.drop_index("ix_payments_ref_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_checkout_attempts_idempotency_key", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_status", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_provider_order_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_revalidation_ref_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_approval_ref_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_basket_ref_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_basket_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_session_id", table_name="checkout_attempts")
    op.drop_index("ix_checkout_attempts_ref_id", table_name="checkout_attempts")
    op.drop_table("checkout_attempts")
