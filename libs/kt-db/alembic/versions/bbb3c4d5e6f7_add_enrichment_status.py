"""Add enrichment_status to nodes and weight_source to edges.

Revision ID: bbb3c4d5e6f7
Revises: aaa1b2c3d4e5
Create Date: 2026-03-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bbb3c4d5e6f7"
down_revision = "zzaa_fact_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nodes",
        sa.Column("enrichment_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "edges",
        sa.Column("weight_source", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edges", "weight_source")
    op.drop_column("nodes", "enrichment_status")
