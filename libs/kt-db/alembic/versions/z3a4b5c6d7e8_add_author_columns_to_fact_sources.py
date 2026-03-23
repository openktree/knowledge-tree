"""Add author_person and author_org columns to fact_sources.

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-03-11
"""

import sqlalchemy as sa
from alembic import op

revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact_sources", sa.Column("author_person", sa.String(500), nullable=True))
    op.add_column("fact_sources", sa.Column("author_org", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("fact_sources", "author_org")
    op.drop_column("fact_sources", "author_person")
