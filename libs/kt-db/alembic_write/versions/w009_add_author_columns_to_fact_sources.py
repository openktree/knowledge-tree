"""Add author_person and author_org columns to write_fact_sources.

Revision ID: w009
Revises: w008
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "w009"
down_revision = "w008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("write_fact_sources", sa.Column("author_person", sa.String(500), nullable=True))
    op.add_column("write_fact_sources", sa.Column("author_org", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("write_fact_sources", "author_org")
    op.drop_column("write_fact_sources", "author_person")
