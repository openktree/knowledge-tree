"""Add fetch_attempted column to write_raw_sources.

Revision ID: w027
Revises: w026
Create Date: 2026-03-19
"""

import sqlalchemy as sa
from alembic import op

revision = "w027"
down_revision = "w026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_raw_sources",
        sa.Column("fetch_attempted", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("write_raw_sources", "fetch_attempted")
