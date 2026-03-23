"""Add content_type column to raw_sources table.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-02-23 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("content_type", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "content_type")
