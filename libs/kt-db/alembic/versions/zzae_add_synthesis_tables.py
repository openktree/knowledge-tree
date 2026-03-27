"""Add node visibility and creator_id columns.

Revision ID: zzae
Revises: hhh9i0j1k2l3
Create Date: 2026-03-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "zzae"
down_revision = "hhh9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add visibility and creator_id to nodes
    op.add_column(
        "nodes",
        sa.Column("visibility", sa.String(20), server_default="public", nullable=False),
    )
    op.add_column(
        "nodes",
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("nodes", "creator_id")
    op.drop_column("nodes", "visibility")
