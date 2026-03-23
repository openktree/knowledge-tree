"""Add fact_count column to raw_sources.

Revision ID: zzaa_fact_count
Revises: zz9y8x7w6v5u
Create Date: 2026-03-13
"""

import sqlalchemy as sa
from alembic import op

revision = "zzaa_fact_count"
down_revision = "aaa1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("fact_count", sa.Integer(), server_default="0", nullable=False),
    )
    # Backfill from existing fact_sources join
    op.execute(
        """
        UPDATE raw_sources rs
        SET fact_count = sub.cnt
        FROM (
            SELECT raw_source_id, COUNT(*) AS cnt
            FROM fact_sources
            GROUP BY raw_source_id
        ) sub
        WHERE rs.id = sub.raw_source_id
        """
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "fact_count")
