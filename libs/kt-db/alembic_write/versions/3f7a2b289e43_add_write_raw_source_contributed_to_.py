"""add write_raw_source contributed_to_public_at

Revision ID: 3f7a2b289e43
Revises: 777cdf5ff9e5
Create Date: 2026-04-08 18:07:22.842810

PR7 of the multigraph public-cache series. Mirrors the graph-db
``raw_sources.contributed_to_public_at`` watermark on the write-db
side. The bridge stamps this column after a successful upstream
contribute, and the sweeper queries it (workers never touch graph-db
so the write-db column is the load-bearing one).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f7a2b289e43"
down_revision: Union[str, None] = "777cdf5ff9e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "write_raw_sources",
        sa.Column("contributed_to_public_at", sa.DateTime(), nullable=True),
    )

    # Partial index for the contribute-retry sweeper. Only includes rows
    # that COULD be candidates for retry: link sources (canonical_url
    # NOT NULL — file uploads never have one) that haven't been
    # successfully contributed yet. Sorted by created_at so the sweeper
    # can age-out stale-but-old rows efficiently.
    op.create_index(
        "ix_write_raw_sources_contribute_pending",
        "write_raw_sources",
        ["created_at"],
        postgresql_where=sa.text("contributed_to_public_at IS NULL AND canonical_url IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_write_raw_sources_contribute_pending",
        table_name="write_raw_sources",
    )
    op.drop_column("write_raw_sources", "contributed_to_public_at")
