"""Type-isolated tree graphs redesign.

- Remove inquiry/method node types (delete nodes with CASCADE)
- Rename parent_concept_id -> parent_id
- Add source_concept_id column for perspectives
- Insert default parent nodes (All Concepts, All Events, All Perspectives)
- Convert edges: delete structural types, convert semantic to "related",
  negate weight for contradicts, delete cross-type edges, dedup
- Clear fact_edge_evaluations

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-02-25 10:00:00.000000
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None

# Deterministic UUIDs for default parent nodes
ALL_CONCEPTS_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-concepts"))
ALL_EVENTS_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-events"))
ALL_PERSPECTIVES_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-perspectives"))


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Delete inquiry/method nodes (CASCADE cleans up facts/edges/dims)
    conn.execute(sa.text("DELETE FROM nodes WHERE node_type IN ('inquiry', 'method')"))

    # 2. Rename column parent_concept_id -> parent_id
    op.alter_column("nodes", "parent_concept_id", new_column_name="parent_id")

    # 3. Add source_concept_id column for perspectives
    op.add_column(
        "nodes",
        sa.Column(
            "source_concept_id",
            sa.UUID(),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 4. Insert 3 default parent nodes
    for node_id, concept, node_type in [
        (ALL_CONCEPTS_ID, "All Concepts", "concept"),
        (ALL_EVENTS_ID, "All Events", "event"),
        (ALL_PERSPECTIVES_ID, "All Perspectives", "perspective"),
    ]:
        conn.execute(
            sa.text(
                "INSERT INTO nodes (id, concept, node_type, max_content_tokens, "
                "stale_after, update_count, access_count, created_at, updated_at) "
                "VALUES (:id, :concept, :node_type, 500, 30, 0, 0, NOW(), NOW()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": node_id, "concept": concept, "node_type": node_type},
        )

    # 5. For perspectives, copy their current parent_id into source_concept_id
    #    (preserving what concept they're about), then set parent_id to All Perspectives
    conn.execute(
        sa.text(
            "UPDATE nodes SET source_concept_id = parent_id WHERE node_type = 'perspective' AND parent_id IS NOT NULL"
        )
    )
    conn.execute(
        sa.text("UPDATE nodes SET parent_id = :default_parent WHERE node_type = 'perspective'"),
        {"default_parent": ALL_PERSPECTIVES_ID},
    )

    # 6. Set orphaned concept/event nodes to their default parents
    conn.execute(
        sa.text(
            "UPDATE nodes SET parent_id = :default_parent "
            "WHERE node_type = 'concept' AND parent_id IS NULL AND id != :default_parent"
        ),
        {"default_parent": ALL_CONCEPTS_ID},
    )
    conn.execute(
        sa.text(
            "UPDATE nodes SET parent_id = :default_parent "
            "WHERE node_type = 'event' AND parent_id IS NULL AND id != :default_parent"
        ),
        {"default_parent": ALL_EVENTS_ID},
    )

    # 7. Convert edges: delete structural types
    conn.execute(sa.text("DELETE FROM edges WHERE relationship_type IN ('perspective_of', 'entity_of')"))

    # 8. Negate weight for contradicts edges
    conn.execute(sa.text("UPDATE edges SET weight = -weight WHERE relationship_type = 'contradicts'"))

    # 9. Convert all remaining edges to "related"
    conn.execute(sa.text("UPDATE edges SET relationship_type = 'related'"))

    # 10. Delete cross-type edges (edges between nodes of different types)
    conn.execute(
        sa.text(
            "DELETE FROM edges WHERE id IN ("
            "  SELECT e.id FROM edges e "
            "  JOIN nodes s ON e.source_node_id = s.id "
            "  JOIN nodes t ON e.target_node_id = t.id "
            "  WHERE s.node_type != t.node_type"
            ")"
        )
    )

    # 11. Dedup: keep highest-weight edge per canonical pair
    # Since all edges are now "related", remove duplicates
    conn.execute(
        sa.text(
            "DELETE FROM edges WHERE id NOT IN ("
            "  SELECT DISTINCT ON (LEAST(source_node_id, target_node_id), "
            "    GREATEST(source_node_id, target_node_id)) id "
            "  FROM edges "
            "  ORDER BY LEAST(source_node_id, target_node_id), "
            "    GREATEST(source_node_id, target_node_id), "
            "    ABS(weight) DESC"
            ")"
        )
    )

    # 12. Clear fact_edge_evaluations
    conn.execute(sa.text("DELETE FROM fact_edge_evaluations"))


def downgrade() -> None:
    # Remove source_concept_id column
    op.drop_column("nodes", "source_concept_id")
    # Rename parent_id back to parent_concept_id
    op.alter_column("nodes", "parent_id", new_column_name="parent_concept_id")
    # Delete default parent nodes
    conn = op.get_bind()
    for node_id in [ALL_CONCEPTS_ID, ALL_EVENTS_ID, ALL_PERSPECTIVES_ID]:
        conn.execute(
            sa.text("DELETE FROM nodes WHERE id = :id"),
            {"id": node_id},
        )
