"""M10 webhook_events + unique provider_payment_id. Client success is not verified.

Revision ID: m10_webhook_events
Revises: m9_checkout_payments
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m10_webhook_events"
down_revision: Union[str, None] = "m9_checkout_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_index(
        "uq_payments_provider_payment_id",
        "payments",
        ["provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("checkout_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("raw_body_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("payload_meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.ForeignKeyConstraint(["checkout_attempt_id"], ["checkout_attempts.id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_webhook_provider_event"),
    )
    op.create_index("ix_webhook_events_ref_id", "webhook_events", ["ref_id"], unique=True)
    op.create_index("ix_webhook_events_session_id", "webhook_events", ["session_id"])
    op.create_index("ix_webhook_events_checkout_attempt_id", "webhook_events", ["checkout_attempt_id"])
    op.create_index("ix_webhook_events_payment_id", "webhook_events", ["payment_id"])
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider"])
    op.create_index("ix_webhook_events_provider_event_id", "webhook_events", ["provider_event_id"])
    op.create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"])
    op.create_index("ix_webhook_events_raw_body_hash", "webhook_events", ["raw_body_hash"])
    op.create_index("ix_webhook_events_provider_order_id", "webhook_events", ["provider_order_id"])
    op.create_index("ix_webhook_events_provider_payment_id", "webhook_events", ["provider_payment_id"])
    op.create_index("ix_webhook_events_processing_status", "webhook_events", ["processing_status"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_processing_status", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider_payment_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider_order_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_raw_body_hash", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider_event_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_index("ix_webhook_events_payment_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_checkout_attempt_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_session_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_ref_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("uq_payments_provider_payment_id", table_name="payments")
