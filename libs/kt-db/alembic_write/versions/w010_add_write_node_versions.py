"""Add write_node_versions table for composite node versioning.

Revision ID: w010
Revises: w009
Create Date: 2026-03-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "w010"
down_revision = "w009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_node_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("node_key", sa.String(500), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB, nullable=True),
        sa.Column("source_node_count", sa.Integer(), server_default="0"),
        sa.Column("is_default", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_write_node_versions_node_key", "write_node_versions", ["node_key"])
    op.create_index("ix_write_node_versions_updated_at", "write_node_versions", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_write_node_versions_updated_at", table_name="write_node_versions")
    op.drop_index("ix_write_node_versions_node_key", table_name="write_node_versions")
    op.drop_table("write_node_versions")
