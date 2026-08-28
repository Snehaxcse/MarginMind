"""M5 friction diagnoses table.

Revision ID: m5_friction_diagnoses
Revises: m3_session_intent_fields
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m5_friction_diagnoses"
down_revision: Union[str, None] = "m3_session_intent_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "friction_diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("friction_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reason_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_friction_diagnoses_ref_id", "friction_diagnoses", ["ref_id"], unique=True)
    op.create_index("ix_friction_diagnoses_session_id", "friction_diagnoses", ["session_id"])
    op.create_index("ix_friction_diagnoses_friction_type", "friction_diagnoses", ["friction_type"])


def downgrade() -> None:
    op.drop_index("ix_friction_diagnoses_friction_type", table_name="friction_diagnoses")
    op.drop_index("ix_friction_diagnoses_session_id", table_name="friction_diagnoses")
    op.drop_index("ix_friction_diagnoses_ref_id", table_name="friction_diagnoses")
    op.drop_table("friction_diagnoses")
