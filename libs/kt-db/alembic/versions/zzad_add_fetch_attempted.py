"""Add fetch_attempted column to raw_sources.

Revision ID: zzad
Revises: zzac
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa

revision = "zzad"
down_revision = "zzac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("fetch_attempted", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "fetch_attempted")
