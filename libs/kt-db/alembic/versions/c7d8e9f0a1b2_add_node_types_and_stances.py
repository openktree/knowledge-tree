"""add_node_types_and_stances

Revision ID: c7d8e9f0a1b2
Revises: b0505ef24d25
Create Date: 2026-02-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b0505ef24d25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mapping from old edge types to the simplified three-type system.
# perspective_of stays as-is; contradicts -> negative; everything else -> positive.
_EDGE_TYPE_MAPPING: dict[str, str] = {
    "contradicts": "negative",
    "perspective_of": "perspective_of",
    # All others -> positive
    "explains": "positive",
    "requires": "positive",
    "related": "positive",
    "supports": "positive",
    "temporal": "positive",
    "causal": "positive",
    "composed_of": "positive",
    "context": "positive",
}


def upgrade() -> None:
    """Add node_type, parent_concept_id to nodes; stance to node_facts; migrate edge types."""
    # --- nodes table ---
    op.add_column("nodes", sa.Column("node_type", sa.String(length=20), server_default="concept", nullable=False))
    op.create_index("ix_nodes_node_type", "nodes", ["node_type"])
    op.add_column("nodes", sa.Column("parent_concept_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_nodes_parent_concept_id",
        "nodes",
        "nodes",
        ["parent_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- node_facts table ---
    op.add_column("node_facts", sa.Column("stance", sa.String(length=20), nullable=True))

    # --- Migrate edge relationship_type values ---
    # First handle the known types via explicit mapping
    for old_type, new_type in _EDGE_TYPE_MAPPING.items():
        if old_type != new_type:
            op.execute(
                sa.text(
                    "UPDATE edges SET relationship_type = :new_type WHERE relationship_type = :old_type"
                ).bindparams(new_type=new_type, old_type=old_type)
            )

    # Catch-all: any remaining types not in our map become "positive"
    op.execute(
        sa.text(
            "UPDATE edges SET relationship_type = 'positive' "
            "WHERE relationship_type NOT IN ('perspective_of', 'positive', 'negative')"
        )
    )


def downgrade() -> None:
    """Remove node_type, parent_concept_id from nodes; stance from node_facts."""
    # Note: edge type migration is not reversible (lossy mapping)
    op.drop_column("node_facts", "stance")
    op.drop_constraint("fk_nodes_parent_concept_id", "nodes", type_="foreignkey")
    op.drop_column("nodes", "parent_concept_id")
    op.drop_index("ix_nodes_node_type", "nodes")
    op.drop_column("nodes", "node_type")
