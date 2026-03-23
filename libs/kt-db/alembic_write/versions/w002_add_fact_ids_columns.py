"""add fact_ids columns to write_edges and write_dimensions

Revision ID: w002
Revises: w001
Create Date: 2026-03-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w002"
down_revision: Union[str, None] = "w001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("write_edges", sa.Column("fact_ids", sa.ARRAY(sa.String), nullable=True))
    op.add_column("write_dimensions", sa.Column("fact_ids", sa.ARRAY(sa.String), nullable=True))


def downgrade() -> None:
    op.drop_column("write_dimensions", "fact_ids")
    op.drop_column("write_edges", "fact_ids")
