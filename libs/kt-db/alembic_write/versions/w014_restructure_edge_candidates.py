"""Restructure edge candidates to one row per (seed_pair, fact)

Revision ID: w014
Revises: w013
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w014"
down_revision = "w013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old table (one row per seed pair with fact array)
    op.drop_table("write_edge_candidates")

    # Create new table (one row per seed_pair + fact)
    op.create_table(
        "write_edge_candidates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("seed_key_a", sa.String(500), nullable=False),
        sa.Column("seed_key_b", sa.String(500), nullable=False),
        sa.Column("fact_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("evaluation_result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("seed_key_a", "seed_key_b", "fact_id", name="uq_wec_pair_fact"),
    )
    op.create_index("ix_wec_seed_a_status", "write_edge_candidates", ["seed_key_a", "status"])
    op.create_index("ix_wec_seed_b_status", "write_edge_candidates", ["seed_key_b", "status"])


def downgrade() -> None:
    op.drop_table("write_edge_candidates")
    # Recreate original structure (data loss is acceptable)
    op.create_table(
        "write_edge_candidates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("seed_key_a", sa.String(500), nullable=False),
        sa.Column("seed_key_b", sa.String(500), nullable=False),
        sa.Column("co_occurring_fact_ids", sa.dialects.postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("fact_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("evaluation_result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("seed_key_a", "seed_key_b", name="uq_wec_pair"),
    )
    op.create_index("ix_write_edge_candidates_updated_at", "write_edge_candidates", ["updated_at"])
