"""M8 revalidation_results. Approval is not success.

Revision ID: m8_revalidation
Revises: m7_policy_decisions
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m8_revalidation"
down_revision: Union[str, None] = "m7_policy_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "revalidation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("basket_id", sa.Uuid(), nullable=True),
        sa.Column("basket_ref_id", sa.String(length=32), nullable=True),
        sa.Column("basket_version", sa.Integer(), nullable=True),
        sa.Column("approval_ref_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("failure_reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("changed_fields", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("state_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("offer_ref_id", sa.String(length=32), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.ForeignKeyConstraint(["basket_id"], ["baskets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_ref_id",
            "state_fingerprint",
            name="uq_revalidation_approval_fingerprint",
        ),
    )
    op.create_index("ix_revalidation_results_ref_id", "revalidation_results", ["ref_id"], unique=True)
    op.create_index("ix_revalidation_results_session_id", "revalidation_results", ["session_id"])
    op.create_index("ix_revalidation_results_basket_id", "revalidation_results", ["basket_id"])
    op.create_index("ix_revalidation_results_approval_ref_id", "revalidation_results", ["approval_ref_id"])
    op.create_index("ix_revalidation_results_status", "revalidation_results", ["status"])
    op.create_index(
        "ix_revalidation_results_state_fingerprint",
        "revalidation_results",
        ["state_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_revalidation_results_state_fingerprint", table_name="revalidation_results")
    op.drop_index("ix_revalidation_results_status", table_name="revalidation_results")
    op.drop_index("ix_revalidation_results_approval_ref_id", table_name="revalidation_results")
    op.drop_index("ix_revalidation_results_basket_id", table_name="revalidation_results")
    op.drop_index("ix_revalidation_results_session_id", table_name="revalidation_results")
    op.drop_index("ix_revalidation_results_ref_id", table_name="revalidation_results")
    op.drop_table("revalidation_results")
