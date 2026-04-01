"""Add write_fetch_skip_domains table.

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
    op.create_table(
        "write_fetch_skip_domains",
        sa.Column("domain", sa.String(255), primary_key=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("write_fetch_skip_domains")
