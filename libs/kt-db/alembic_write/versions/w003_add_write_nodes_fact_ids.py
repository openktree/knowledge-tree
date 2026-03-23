"""add fact_ids column to write_nodes

Revision ID: w003
Revises: w002
Create Date: 2026-03-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w003"
down_revision: Union[str, None] = "w002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("write_nodes", sa.Column("fact_ids", sa.ARRAY(sa.String), nullable=True))


def downgrade() -> None:
    op.drop_column("write_nodes", "fact_ids")
