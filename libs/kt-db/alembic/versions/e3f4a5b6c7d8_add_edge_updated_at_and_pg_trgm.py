"""Add updated_at to edges and pg_trgm extension.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-02-24 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column("edges", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE edges SET updated_at = created_at")
    op.alter_column("edges", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("edges", "updated_at")
