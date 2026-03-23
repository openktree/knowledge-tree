"""remove write_key columns from nodes and edges (deterministic UUIDs instead)

Revision ID: v8w9x0y1z2a3
Revises: 636fa81392ad
Create Date: 2026-03-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "v8w9x0y1z2a3"
down_revision: Union[str, None] = "636fa81392ad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # With deterministic UUIDs derived from write keys,
    # write_key columns are no longer needed on graph-db tables.
    # Uses IF EXISTS so this is a safe no-op on fresh databases.
    op.execute("DROP INDEX IF EXISTS ix_nodes_write_key")
    op.execute("ALTER TABLE nodes DROP CONSTRAINT IF EXISTS uq_nodes_write_key")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS write_key")

    op.execute("DROP INDEX IF EXISTS ix_edges_write_key")
    op.execute("ALTER TABLE edges DROP CONSTRAINT IF EXISTS uq_edges_write_key")
    op.execute("ALTER TABLE edges DROP COLUMN IF EXISTS write_key")


def downgrade() -> None:
    op.add_column("nodes", sa.Column("write_key", sa.String(500), nullable=True))
    op.create_unique_constraint("uq_nodes_write_key", "nodes", ["write_key"])
    op.create_index("ix_nodes_write_key", "nodes", ["write_key"])

    op.add_column("edges", sa.Column("write_key", sa.String(1200), nullable=True))
    op.create_unique_constraint("uq_edges_write_key", "edges", ["write_key"])
    op.create_index("ix_edges_write_key", "edges", ["write_key"])
