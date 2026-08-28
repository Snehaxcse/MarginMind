"""M3 session event actor/evidence refs and intent persistence columns.

Revision ID: m3_session_intent_fields
Revises: m1_initial
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m3_session_intent_fields"
down_revision: Union[str, None] = "m1_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "session_events",
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
    )
    op.add_column(
        "session_events",
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    op.add_column("intents", sa.Column("customer_id", sa.Uuid(), nullable=True))
    op.add_column("intents", sa.Column("usual_size", sa.String(length=16), nullable=True))
    op.add_column(
        "intents",
        sa.Column("colour_preferences", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "intents",
        sa.Column("excluded_materials", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "intents",
        sa.Column("excluded_coverage", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("intents", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column(
        "intents",
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "intents",
        sa.Column("missing_fields", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "intents",
        sa.Column("ambiguities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index("ix_intents_customer_id", "intents", ["customer_id"])
    op.create_foreign_key(
        "fk_intents_customer_id",
        "intents",
        "customers",
        ["customer_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_intents_customer_id", "intents", type_="foreignkey")
    op.drop_index("ix_intents_customer_id", table_name="intents")
    op.drop_column("intents", "ambiguities")
    op.drop_column("intents", "missing_fields")
    op.drop_column("intents", "evidence_ref_ids")
    op.drop_column("intents", "confidence")
    op.drop_column("intents", "excluded_coverage")
    op.drop_column("intents", "excluded_materials")
    op.drop_column("intents", "colour_preferences")
    op.drop_column("intents", "usual_size")
    op.drop_column("intents", "customer_id")
    op.drop_column("session_events", "evidence_ref_ids")
    op.drop_column("session_events", "actor")
