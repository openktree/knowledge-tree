"""Denormalize node stats onto nodes table + add seed_fact_count to node_counters.

Adds fact_count, edge_count, child_count, dimension_count, convergence_score
directly to the nodes table so API/MCP endpoints can read them without
running 7-8 separate batch queries per request.

Also adds seed_fact_count to node_counters so the sync worker can propagate
write-db seed counts without a cross-DB query at read time.

Revision ID: zzag
Revises: zzaf
Create Date: 2026-03-30
"""

import sqlalchemy as sa
from alembic import op

revision = "zzag"
down_revision = "zzaf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add denormalized counter columns to nodes
    op.add_column("nodes", sa.Column("fact_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("nodes", sa.Column("edge_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("nodes", sa.Column("child_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("nodes", sa.Column("dimension_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("nodes", sa.Column("convergence_score", sa.Float(), server_default="0.0", nullable=False))

    # Add seed_fact_count to node_counters
    op.add_column("node_counters", sa.Column("seed_fact_count", sa.Integer(), server_default="0", nullable=False))

    # Backfill existing data
    op.execute(
        """
        UPDATE nodes SET fact_count = sub.cnt
        FROM (
            SELECT node_id, COUNT(*) AS cnt
            FROM node_facts
            GROUP BY node_id
        ) sub
        WHERE nodes.id = sub.node_id
        """
    )

    op.execute(
        """
        UPDATE nodes SET edge_count = sub.cnt
        FROM (
            SELECT node_id, COUNT(*) AS cnt
            FROM (
                SELECT source_node_id AS node_id FROM edges
                UNION ALL
                SELECT target_node_id AS node_id FROM edges
            ) all_edges
            GROUP BY node_id
        ) sub
        WHERE nodes.id = sub.node_id
        """
    )

    op.execute(
        """
        UPDATE nodes SET child_count = sub.cnt
        FROM (
            SELECT parent_id, COUNT(*) AS cnt
            FROM nodes
            WHERE parent_id IS NOT NULL
            GROUP BY parent_id
        ) sub
        WHERE nodes.id = sub.parent_id
        """
    )

    op.execute(
        """
        UPDATE nodes SET dimension_count = sub.cnt
        FROM (
            SELECT node_id, COUNT(*) AS cnt
            FROM dimensions
            GROUP BY node_id
        ) sub
        WHERE nodes.id = sub.node_id
        """
    )

    op.execute(
        """
        UPDATE nodes SET convergence_score = sub.score
        FROM (
            SELECT node_id, convergence_score AS score
            FROM convergence_reports
        ) sub
        WHERE nodes.id = sub.node_id
        """
    )


def downgrade() -> None:
    op.drop_column("node_counters", "seed_fact_count")
    op.drop_column("nodes", "convergence_score")
    op.drop_column("nodes", "dimension_count")
    op.drop_column("nodes", "child_count")
    op.drop_column("nodes", "edge_count")
    op.drop_column("nodes", "fact_count")
