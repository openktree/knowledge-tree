"""Drop write_fact_edge_evaluations table (replaced by candidate-level rejection)

Revision ID: w015
Revises: w014
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w015"
down_revision = "w014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("write_fact_edge_evaluations")


def downgrade() -> None:
    op.create_table(
        "write_fact_edge_evaluations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source_node_id", sa.UUID(), nullable=False),
        sa.Column("target_node_id", sa.UUID(), nullable=False),
        sa.Column("fact_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_write_fee_updated_at", "write_fact_edge_evaluations", ["updated_at"])
    op.create_index("ix_write_fee_source_target", "write_fact_edge_evaluations", ["source_node_id", "target_node_id"])
    op.create_index("uq_write_fee", "write_fact_edge_evaluations", ["source_node_id", "target_node_id", "fact_id"], unique=True)
