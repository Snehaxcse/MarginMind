"""M6 agent_actions table for proposed (not authorized) GDE decisions.

Revision ID: m6_agent_actions
Revises: m5_friction_diagnoses
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m6_agent_actions"
down_revision: Union[str, None] = "m5_friction_diagnoses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ref_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("friction_ref_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_ref_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("candidate_skus", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("offer_ref_id", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("requires_policy_check", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_customer_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("potential_revenue_not_pursued", sa.Numeric(10, 2), nullable=True),
        sa.Column("what", sa.Text(), nullable=False, server_default=""),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("fix", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PROPOSED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shopping_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_actions_ref_id", "agent_actions", ["ref_id"], unique=True)
    op.create_index("ix_agent_actions_session_id", "agent_actions", ["session_id"])
    op.create_index("ix_agent_actions_friction_ref_id", "agent_actions", ["friction_ref_id"])
    op.create_index("ix_agent_actions_action", "agent_actions", ["action"])


def downgrade() -> None:
    op.drop_index("ix_agent_actions_action", table_name="agent_actions")
    op.drop_index("ix_agent_actions_friction_ref_id", table_name="agent_actions")
    op.drop_index("ix_agent_actions_session_id", table_name="agent_actions")
    op.drop_index("ix_agent_actions_ref_id", table_name="agent_actions")
    op.drop_table("agent_actions")
