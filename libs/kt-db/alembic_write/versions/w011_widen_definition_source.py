"""widen definition_source column to varchar(100)

Revision ID: w011
Revises: w010
Create Date: 2026-03-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w011"
down_revision = "w010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "write_nodes",
        "definition_source",
        type_=sa.String(100),
        existing_type=sa.String(20),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "write_nodes",
        "definition_source",
        type_=sa.String(20),
        existing_type=sa.String(100),
        existing_nullable=True,
    )
