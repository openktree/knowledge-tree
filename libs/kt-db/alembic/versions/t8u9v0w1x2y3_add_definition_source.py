"""add definition_source column to nodes

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-03-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t8u9v0w1x2y3"
down_revision = "s7t8u9v0w1x2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("definition_source", sa.String(20), nullable=True))
    # Backfill: crystallized nodes get 'crystallized', others get 'synthesized'
    op.execute(
        "UPDATE nodes SET definition_source = 'crystallized' "
        "WHERE definition IS NOT NULL AND metadata->>'ontology_stable' = 'true'"
    )
    op.execute(
        "UPDATE nodes SET definition_source = 'synthesized' WHERE definition IS NOT NULL AND definition_source IS NULL"
    )


def downgrade() -> None:
    op.drop_column("nodes", "definition_source")
