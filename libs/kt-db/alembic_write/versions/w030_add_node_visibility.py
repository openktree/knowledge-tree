"""Add visibility and creator_id to write_nodes.

Revision ID: w030
Revises: w029
Create Date: 2026-03-25
"""

import sqlalchemy as sa
from alembic import op

revision = "w030"
down_revision = "w029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_nodes",
        sa.Column("visibility", sa.String(20), server_default="public", nullable=False),
    )
    op.add_column(
        "write_nodes",
        sa.Column("creator_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("write_nodes", "creator_id")
    op.drop_column("write_nodes", "visibility")
