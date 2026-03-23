"""merge page_fetch_log and drop_embedding_columns heads

Revision ID: 1b47bc13d4f5
Revises: u9v0w1x2y3z4, w9x0y1z2a3b4
Create Date: 2026-03-09 12:12:20.576683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b47bc13d4f5'
down_revision: Union[str, Sequence[str], None] = ('u9v0w1x2y3z4', 'w9x0y1z2a3b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
