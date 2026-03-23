"""Add is_super_source to raw_sources and super_sources to research_reports.

Revision ID: zzab
Revises: fff7g8h9i0j1
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "zzab"
down_revision = "fff7g8h9i0j1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("is_super_source", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "research_reports",
        sa.Column("super_sources", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_reports", "super_sources")
    op.drop_column("raw_sources", "is_super_source")
