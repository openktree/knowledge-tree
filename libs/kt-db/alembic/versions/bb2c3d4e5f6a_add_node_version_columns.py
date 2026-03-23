"""Add source_node_count and is_default to node_versions.

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
Create Date: 2026-03-11

"""

import sqlalchemy as sa
from alembic import op

revision = "bb2c3d4e5f6a"
down_revision = "aa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("node_versions", sa.Column("source_node_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("node_versions", sa.Column("is_default", sa.Boolean(), nullable=True, server_default="false"))


def downgrade() -> None:
    op.drop_column("node_versions", "is_default")
    op.drop_column("node_versions", "source_node_count")
