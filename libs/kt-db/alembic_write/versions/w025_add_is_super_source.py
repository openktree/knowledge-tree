"""Add is_super_source to write_raw_sources.

Revision ID: w025
Revises: w024
Create Date: 2026-03-18
"""

import sqlalchemy as sa
from alembic import op

revision = "w025"
down_revision = "w024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_raw_sources",
        sa.Column("is_super_source", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("write_raw_sources", "is_super_source")
