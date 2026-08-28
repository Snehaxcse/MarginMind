"""M7 policy_decisions table and approval binding columns.

Revision ID: m7_policy_decisions
Revises: m6_agent_actions
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m7_policy_decisions"
down_revision: Union[str, None] = "m6_agent_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("approvals", sa.Column("action_ref_id", sa.String(length=32), nullable=True))
    op.add_column(
        "approvals",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.create_index("ix_approvals_action_ref_id", "approvals", ["action_ref_id"])

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("action_ref_id", sa.String(length=32), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_customer_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_merchant_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("checks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_decisions_ref_id", "policy_decisions", ["ref_id"], unique=True)
    op.create_index("ix_policy_decisions_session_id", "policy_decisions", ["session_id"])
    op.create_index("ix_policy_decisions_action_ref_id", "policy_decisions", ["action_ref_id"])
    op.create_index("ix_policy_decisions_decision", "policy_decisions", ["decision"])


def downgrade() -> None:
    op.drop_index("ix_policy_decisions_decision", table_name="policy_decisions")
    op.drop_index("ix_policy_decisions_action_ref_id", table_name="policy_decisions")
    op.drop_index("ix_policy_decisions_session_id", table_name="policy_decisions")
    op.drop_index("ix_policy_decisions_ref_id", table_name="policy_decisions")
    op.drop_table("policy_decisions")
    op.drop_index("ix_approvals_action_ref_id", table_name="approvals")
    op.drop_column("approvals", "status")
    op.drop_column("approvals", "action_ref_id")
