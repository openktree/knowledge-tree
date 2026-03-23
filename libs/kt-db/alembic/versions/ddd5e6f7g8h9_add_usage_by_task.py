"""Add usage_by_task JSONB column to research_reports.

Revision ID: ddd5e6f7g8h9
Revises: ccc4d5e6f7g8
Create Date: 2026-03-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ddd5e6f7g8h9"
down_revision = "ccc4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_reports",
        sa.Column("usage_by_task", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_reports", "usage_by_task")
