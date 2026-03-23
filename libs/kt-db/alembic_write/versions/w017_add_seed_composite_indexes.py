"""Add composite indexes for seed dedup query performance

Revision ID: w017
Revises: w016
Create Date: 2026-03-13
"""

from __future__ import annotations

from alembic import op

revision = "w017"
down_revision = "w016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite index for find_similar_seeds() and find_by_phonetic()
    # which both filter by (node_type, status) before trigram/phonetic lookup
    op.create_index(
        "ix_write_seeds_type_status",
        "write_seeds",
        ["node_type", "status"],
    )

    # fact_id index on write_seed_facts for merge operations
    # (merge_seeds reassigns facts by seed_key but also joins on fact_id)
    op.create_index(
        "ix_write_seed_facts_fact_id",
        "write_seed_facts",
        ["fact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_write_seed_facts_fact_id", table_name="write_seed_facts")
    op.drop_index("ix_write_seeds_type_status", table_name="write_seeds")
