"""Add fetch_error column to write_raw_sources.

Revision ID: w033
Revises: w032
Create Date: 2026-04-01
"""

import sqlalchemy as sa
from alembic import op

revision = "w033"
down_revision = "w032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_raw_sources",
        sa.Column("fetch_error", sa.String(1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("write_raw_sources", "fetch_error")
