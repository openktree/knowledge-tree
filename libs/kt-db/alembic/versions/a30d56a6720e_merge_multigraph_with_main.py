"""merge multigraph with main

Revision ID: a30d56a6720e
Revises: c31dff170411, fcd2d573ed5e, zzaj
Create Date: 2026-04-06 08:28:24.652855

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a30d56a6720e'
down_revision: Union[str, Sequence[str], None] = ('c31dff170411', 'fcd2d573ed5e', 'zzaj')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
