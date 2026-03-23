"""Add enrichment_status to write_nodes and weight_source to write_edges.

Revision ID: w022
Revises: w021
"""

from alembic import op
import sqlalchemy as sa

revision = "w022"
down_revision = "w021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_nodes",
        sa.Column("enrichment_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "write_edges",
        sa.Column("weight_source", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("write_edges", "weight_source")
    op.drop_column("write_nodes", "enrichment_status")
