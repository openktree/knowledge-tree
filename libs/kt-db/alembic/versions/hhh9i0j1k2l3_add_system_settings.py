"""Add system_settings table.

Revision ID: hhh9i0j1k2l3
Revises: ggg8h9i0j1k2
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa


revision = "hhh9i0j1k2l3"
down_revision = "ggg8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
