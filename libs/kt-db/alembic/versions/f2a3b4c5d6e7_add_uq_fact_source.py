"""Add unique constraint on fact_sources(fact_id, raw_source_id).

Deduplicates existing rows first, keeping the one with the longest context_snippet.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-02-22 12:00:00.000000
"""

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delete duplicate fact_source rows, keeping the one with the longest
    # context_snippet per (fact_id, raw_source_id) group.
    op.execute("""
        DELETE FROM fact_sources
        WHERE id NOT IN (
            SELECT DISTINCT ON (fact_id, raw_source_id) id
            FROM fact_sources
            ORDER BY fact_id, raw_source_id,
                     COALESCE(LENGTH(context_snippet), 0) DESC
        )
    """)

    op.create_unique_constraint("uq_fact_source", "fact_sources", ["fact_id", "raw_source_id"])


def downgrade() -> None:
    op.drop_constraint("uq_fact_source", "fact_sources", type_="unique")
