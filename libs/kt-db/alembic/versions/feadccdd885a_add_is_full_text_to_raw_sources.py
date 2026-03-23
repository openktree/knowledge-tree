"""add_is_full_text_to_raw_sources

Revision ID: feadccdd885a
Revises: c7d8e9f0a1b2
Create Date: 2026-02-18 13:28:57.653949

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'feadccdd885a'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_full_text boolean column to raw_sources."""
    op.add_column('raw_sources', sa.Column('is_full_text', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    """Remove is_full_text column from raw_sources."""
    op.drop_column('raw_sources', 'is_full_text')
