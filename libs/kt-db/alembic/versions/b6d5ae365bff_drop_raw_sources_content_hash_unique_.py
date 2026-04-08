"""drop raw_sources content_hash unique constraint

Revision ID: b6d5ae365bff
Revises: f982135a0b7f
Create Date: 2026-04-07 18:03:35.038768

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b6d5ae365bff'
down_revision: Union[str, Sequence[str], None] = 'f982135a0b7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop UNIQUE on raw_sources.content_hash, keep a non-unique index.

    write-db is the source of truth for source dedup; the secondary unique
    index on the graph-db projection only ever wedged worker-sync when the
    two databases disagreed on which id carried a given hash.
    """
    op.drop_index(op.f("ix_raw_sources_content_hash"), table_name="raw_sources")
    op.create_index(
        op.f("ix_raw_sources_content_hash"),
        "raw_sources",
        ["content_hash"],
        unique=False,
    )


def downgrade() -> None:
    """Restore UNIQUE on raw_sources.content_hash."""
    op.drop_index(op.f("ix_raw_sources_content_hash"), table_name="raw_sources")
    op.create_index(
        op.f("ix_raw_sources_content_hash"),
        "raw_sources",
        ["content_hash"],
        unique=True,
    )
