"""Drop fact_edge_evaluations table (replaced by candidate-level rejection)

Revision ID: aaa1b2c3d4e5
Revises: zz9y8x7w6v5u
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aaa1b2c3d4e5"
down_revision = "zz9y8x7w6v5u"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("fact_edge_evaluations")


def downgrade() -> None:
    op.create_table(
        "fact_edge_evaluations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source_node_id", sa.UUID(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_node_id", sa.UUID(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_id", sa.UUID(), sa.ForeignKey("facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("source_node_id", "target_node_id", "fact_id", name="uq_fact_edge_eval"),
    )
    op.create_index("ix_fact_edge_eval_source", "fact_edge_evaluations", ["source_node_id"])
    op.create_index("ix_fact_edge_eval_target", "fact_edge_evaluations", ["target_node_id"])
