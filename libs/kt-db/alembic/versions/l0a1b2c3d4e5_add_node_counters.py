"""Add node_counters table to reduce row-lock contention on nodes.

Moves access_count and update_count to a separate table so that
concurrent counter increments don't block each other or conflict
with node content updates.

Revision ID: l0a1b2c3d4e5
Revises: k9f0a1b2c3d4
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "l0a1b2c3d4e5"
down_revision = "k9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_counters",
        sa.Column("node_id", UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("update_count", sa.Integer, nullable=False, server_default="0"),
    )

    # Migrate existing counter values
    op.execute(
        """
        INSERT INTO node_counters (node_id, access_count, update_count)
        SELECT id, access_count, update_count FROM nodes
        """
    )


def downgrade() -> None:
    # Copy counters back to nodes before dropping
    op.execute(
        """
        UPDATE nodes SET
            access_count = nc.access_count,
            update_count = nc.update_count
        FROM node_counters nc
        WHERE nodes.id = nc.node_id
        """
    )
    op.drop_table("node_counters")
