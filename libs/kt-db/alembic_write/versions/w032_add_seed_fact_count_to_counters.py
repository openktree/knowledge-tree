"""Add seed_fact_count to write_node_counters.

Revision ID: w032
Revises: w031
Create Date: 2026-03-30
"""

import sqlalchemy as sa
from alembic import op

revision = "w032"
down_revision = "w031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_node_counters",
        sa.Column("seed_fact_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("write_node_counters", "seed_fact_count")
