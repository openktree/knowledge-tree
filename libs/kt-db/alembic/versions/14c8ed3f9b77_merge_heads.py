"""merge_heads

Revision ID: 14c8ed3f9b77
Revises: 37d967d5e999, 3af9b510fd78
Create Date: 2026-03-31 13:13:33.436463

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '14c8ed3f9b77'
down_revision: Union[str, Sequence[str], None] = ('37d967d5e999', '3af9b510fd78')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
