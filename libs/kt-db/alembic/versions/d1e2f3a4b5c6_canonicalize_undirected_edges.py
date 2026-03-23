"""canonicalize_undirected_edges

Revision ID: d1e2f3a4b5c6
Revises: feadccdd885a
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'feadccdd885a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Canonicalize undirected edges: smaller UUID always in source_node_id.

    1. Delete duplicate reversed pairs (keep the one with higher weight,
       tiebreak by newer created_at).
    2. Swap source/target for remaining non-canonical rows.
    """
    undirected_types = "('positive', 'negative', 'entity_of')"

    # Step 1: Remove reversed duplicates.
    # For each pair (A,B) and (B,A) of the same undirected type, keep the
    # row with the higher weight (tiebreak: newer created_at).
    op.execute(f"""
        DELETE FROM edges
        WHERE id IN (
            SELECT e2.id
            FROM edges e1
            JOIN edges e2
                ON  e1.source_node_id = e2.target_node_id
                AND e1.target_node_id = e2.source_node_id
                AND e1.relationship_type = e2.relationship_type
                AND e1.source_node_id < e1.target_node_id   -- e1 is already canonical
                AND e2.source_node_id > e2.target_node_id   -- e2 is the reverse
            WHERE e1.relationship_type IN {undirected_types}
              AND (
                  e2.weight < e1.weight
                  OR (e2.weight = e1.weight AND e2.created_at <= e1.created_at)
              )
        )
    """)

    # Step 2: Swap source/target for remaining non-canonical edges.
    op.execute(f"""
        UPDATE edges
        SET source_node_id = target_node_id,
            target_node_id = source_node_id
        WHERE relationship_type IN {undirected_types}
          AND source_node_id > target_node_id
    """)


def downgrade() -> None:
    """No-op: edge direction is semantically meaningless for undirected types."""
    pass
