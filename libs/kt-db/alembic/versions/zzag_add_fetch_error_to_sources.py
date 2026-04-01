"""Add fetch_error column to raw_sources.

Revision ID: zzag
Revises: zzaf
Create Date: 2026-04-01
"""

import sqlalchemy as sa
from alembic import op

revision = "zzag"
down_revision = "zzaf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("fetch_error", sa.String(1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "fetch_error")
