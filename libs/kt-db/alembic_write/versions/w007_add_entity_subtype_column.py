"""add entity_subtype column to write_nodes

Revision ID: w007
Revises: w006
Create Date: 2026-03-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "w007"
down_revision: Union[str, None] = "w006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("write_nodes", sa.Column("entity_subtype", sa.String(20), nullable=True))
    op.create_index("ix_write_nodes_entity_subtype", "write_nodes", ["entity_subtype"])


def downgrade() -> None:
    op.drop_index("ix_write_nodes_entity_subtype", table_name="write_nodes")
    op.drop_column("write_nodes", "entity_subtype")
