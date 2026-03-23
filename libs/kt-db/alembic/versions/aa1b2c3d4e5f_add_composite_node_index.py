"""Add partial index on nodes for composite node types.

Revision ID: aa1b2c3d4e5f
Revises: z3a4b5c6d7e8
Create Date: 2026-03-11
"""

from alembic import op

revision = "aa1b2c3d4e5f"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_nodes_composite ON nodes (node_type) WHERE node_type IN ('synthesis', 'perspective')")


def downgrade() -> None:
    op.drop_index("ix_nodes_composite", table_name="nodes")
