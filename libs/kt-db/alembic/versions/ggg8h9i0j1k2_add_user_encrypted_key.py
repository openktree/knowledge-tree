"""Add encrypted_openrouter_key column to user table.

Revision ID: ggg8h9i0j1k2
Revises: fff7g8h9i0j1
Create Date: 2026-03-20
"""

import sqlalchemy as sa
from alembic import op

revision = "ggg8h9i0j1k2"
down_revision = "zzad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("encrypted_openrouter_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "encrypted_openrouter_key")
