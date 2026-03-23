"""Expand undirected edge types and deduplicate edges.

Three steps:
1. For newly-undirected types (supports, part_of, used_for), delete reverse
   duplicates -- when both A->B and B->A exist, keep the higher-weight row.
2. Canonicalize surviving rows so source_node_id < target_node_id.
3. Cross-type dedup: for each canonical node pair with multiple non-structural
   edges, keep only the highest-weight edge.

Revision ID: d2e3f4a5b6c7
Revises: c5d6e7f8a9b0
Create Date: 2026-02-23 18:00:00.000000
"""

from alembic import op


revision = "d2e3f4a5b6c7"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None

# Types that are becoming undirected in this migration
NEWLY_UNDIRECTED = ("supports", "part_of", "used_for")

# Structural types are exempt from cross-type dedup
STRUCTURAL_TYPES = ("perspective_of", "entity_of")


def upgrade() -> None:
    # -- Step 1: Remove reverse duplicates for newly-undirected types --
    # When both A->B and B->A exist for the same type, delete the lower-weight
    # row.  If weights are equal, delete the row where source > target (i.e.
    # the non-canonical direction).
    for etype in NEWLY_UNDIRECTED:
        op.execute(f"""
            DELETE FROM edges e1
            USING edges e2
            WHERE e1.relationship_type = '{etype}'
              AND e2.relationship_type = '{etype}'
              AND e1.source_node_id = e2.target_node_id
              AND e1.target_node_id = e2.source_node_id
              AND e1.id != e2.id
              AND (
                  e1.weight < e2.weight
                  OR (e1.weight = e2.weight AND e1.source_node_id > e1.target_node_id)
              )
        """)

    # -- Step 2: Canonicalize remaining rows (swap source/target) --
    for etype in NEWLY_UNDIRECTED:
        op.execute(f"""
            UPDATE edges
            SET source_node_id = target_node_id,
                target_node_id = source_node_id
            WHERE relationship_type = '{etype}'
              AND source_node_id > target_node_id
        """)

    # -- Step 3: Cross-type dedup --
    # For each canonical (source, target) pair that has multiple non-structural
    # edges, keep only the highest-weight edge.
    op.execute("""
        DELETE FROM edges
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY
                               LEAST(source_node_id, target_node_id),
                               GREATEST(source_node_id, target_node_id)
                           ORDER BY weight DESC, created_at ASC
                       ) AS rn
                FROM edges
                WHERE relationship_type NOT IN ('perspective_of', 'entity_of')
            ) ranked
            WHERE rn > 1
        )
    """)


def downgrade() -> None:
    # Data-only migration -- downgrade is a no-op (deleted rows cannot be
    # recovered, but the schema is unchanged).
    pass
