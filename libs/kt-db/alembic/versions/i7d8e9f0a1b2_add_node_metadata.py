"""Add metadata JSONB column to nodes.

Revision ID: i7d8e9f0a1b2
Revises: h6c7d8e9f0a1
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "i7d8e9f0a1b2"
down_revision = "h6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("metadata", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "metadata")
