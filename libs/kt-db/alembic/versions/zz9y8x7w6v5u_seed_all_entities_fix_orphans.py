"""Seed 'All Entities' root node and fix orphaned parent chains.

- Insert 'All Entities' root node so entity-type nodes have a proper root
- Fix 'All Perspectives' self-referential parent (set to NULL like other roots)
- Connect orphaned nodes to their type-appropriate root:
  - concept nodes with parent_id=NULL -> All Concepts
  - event nodes with parent_id=NULL -> All Events
  - perspective nodes with parent_id=NULL -> All Perspectives
  - entity nodes with parent_id=NULL -> All Entities
  - location nodes with parent_id=NULL -> All Concepts (locations are concept-type)

Revision ID: zz9y8x7w6v5u
Revises: z3a4b5c6d7e8
Create Date: 2026-03-11
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "zz9y8x7w6v5u"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None

# Deterministic UUIDs matching kt_config.types
ALL_CONCEPTS_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-concepts"))
ALL_EVENTS_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-events"))
ALL_PERSPECTIVES_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-perspectives"))
ALL_ENTITIES_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-tree.all-entities"))


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Insert 'All Entities' root node
    conn.execute(
        sa.text(
            "INSERT INTO nodes (id, concept, node_type, max_content_tokens, "
            "stale_after, update_count, access_count, created_at, updated_at) "
            "VALUES (:id, :concept, :node_type, 500, 30, 0, 0, NOW(), NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": ALL_ENTITIES_ID, "concept": "All Entities", "node_type": "entity"},
    )

    # 2. Fix 'All Perspectives' self-referential parent
    conn.execute(
        sa.text("UPDATE nodes SET parent_id = NULL WHERE id = :id AND parent_id = :id"),
        {"id": ALL_PERSPECTIVES_ID},
    )

    # 3. Connect orphaned nodes to their type-appropriate root.
    # Skip the root nodes themselves (they should have parent_id=NULL).
    for node_type, root_id in [
        ("concept", ALL_CONCEPTS_ID),
        ("event", ALL_EVENTS_ID),
        ("perspective", ALL_PERSPECTIVES_ID),
        ("entity", ALL_ENTITIES_ID),
        ("location", ALL_CONCEPTS_ID),
    ]:
        conn.execute(
            sa.text(
                "UPDATE nodes SET parent_id = :root_id "
                "WHERE parent_id IS NULL "
                "AND node_type = :node_type "
                "AND id NOT IN (:r1, :r2, :r3, :r4)"
            ),
            {
                "root_id": root_id,
                "node_type": node_type,
                "r1": ALL_CONCEPTS_ID,
                "r2": ALL_EVENTS_ID,
                "r3": ALL_PERSPECTIVES_ID,
                "r4": ALL_ENTITIES_ID,
            },
        )


def downgrade() -> None:
    # Not reversible — we can't know which nodes were originally orphaned
    pass
