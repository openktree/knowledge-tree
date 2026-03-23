"""widen definition_source column to varchar(100)

Revision ID: a4b5c6d7e8f9
Revises: z3a4b5c6d7e8
Create Date: 2026-03-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "bb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "nodes",
        "definition_source",
        type_=sa.String(100),
        existing_type=sa.String(20),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "nodes",
        "definition_source",
        type_=sa.String(20),
        existing_type=sa.String(100),
        existing_nullable=True,
    )
