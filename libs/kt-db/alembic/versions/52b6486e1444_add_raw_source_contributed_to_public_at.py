"""add raw_source contributed_to_public_at

Revision ID: 52b6486e1444
Revises: 38859c06de60
Create Date: 2026-04-08 18:07:13.684109

PR7 of the multigraph public-cache series. Adds a watermark column to
``raw_sources`` so the contribute-retry sweeper can find rows that
should have been pushed to the public default graph but weren't (e.g.
the worker crashed mid-contribute, the default graph was unreachable,
the upstream Qdrant collection wasn't ready yet).

The column is per-schema because raw_sources is per-schema. Workers
write the watermark via WriteRawSource (PR7's write-db migration);
this graph-db column exists so the API + reads see the same value
after the sync worker propagates it.
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "52b6486e1444"
down_revision: Union[str, Sequence[str], None] = "38859c06de60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-schema: raw_sources gains the watermark column. The sweeper
    # actually queries write-db (workers can't touch graph-db), but the
    # graph-db side has the column too so reads stay consistent.
    op.add_column(
        "raw_sources",
        sa.Column("contributed_to_public_at", sa.DateTime(), nullable=True),
    )

    # Partial index: only the rows the sweeper actually scans. Avoids
    # bloating the index with all the rows that are already contributed.
    # Sorted by ``retrieved_at`` (the graph-db RawSource doesn't have a
    # ``created_at``; ``retrieved_at`` is the closest equivalent and is
    # what the sweeper would order by if it queried graph-db directly).
    schema = os.environ.get("ALEMBIC_SCHEMA")
    op.create_index(
        "ix_raw_sources_contribute_pending",
        "raw_sources",
        ["retrieved_at"],
        postgresql_where=sa.text("contributed_to_public_at IS NULL AND canonical_url IS NOT NULL"),
        schema=schema if schema and schema != "public" else None,
    )


def downgrade() -> None:
    schema = os.environ.get("ALEMBIC_SCHEMA")
    op.drop_index(
        "ix_raw_sources_contribute_pending",
        table_name="raw_sources",
        schema=schema if schema and schema != "public" else None,
    )
    op.drop_column("raw_sources", "contributed_to_public_at")
