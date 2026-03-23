"""Add mode column to conversations table.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-02-22 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="research"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "mode")
