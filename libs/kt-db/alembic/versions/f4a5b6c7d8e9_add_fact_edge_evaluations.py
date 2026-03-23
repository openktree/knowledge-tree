"""Add fact_edge_evaluations table.

Tracks facts evaluated for edge resolution that didn't produce an edge,
preventing redundant LLM re-evaluation of the same facts for the same
node pair.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-02-24 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_edge_evaluations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source_node_id", sa.UUID(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_node_id", sa.UUID(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_id", sa.UUID(), sa.ForeignKey("facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_node_id", "target_node_id", "fact_id", name="uq_fact_edge_eval"),
    )
    op.create_index("ix_fact_edge_eval_source", "fact_edge_evaluations", ["source_node_id"])
    op.create_index("ix_fact_edge_eval_target", "fact_edge_evaluations", ["target_node_id"])


def downgrade() -> None:
    op.drop_index("ix_fact_edge_eval_target", table_name="fact_edge_evaluations")
    op.drop_index("ix_fact_edge_eval_source", table_name="fact_edge_evaluations")
    op.drop_table("fact_edge_evaluations")
