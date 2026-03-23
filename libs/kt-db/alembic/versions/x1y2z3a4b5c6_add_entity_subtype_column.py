"""add entity_subtype column to nodes

Revision ID: x1y2z3a4b5c6
Revises: 1b47bc13d4f5
Create Date: 2026-03-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, None] = "1b47bc13d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("entity_subtype", sa.String(20), nullable=True))
    op.create_index("ix_nodes_entity_subtype", "nodes", ["entity_subtype"])


def downgrade() -> None:
    op.drop_index("ix_nodes_entity_subtype", table_name="nodes")
    op.drop_column("nodes", "entity_subtype")
