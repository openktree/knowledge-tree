"""Add access_groups column to write_fact_sources.

Denormalized source-level access control for the write-db.
Sync worker propagates this to graph-db RawSource.access_groups.

Revision ID: w036
Revises: w035
Create Date: 2026-04-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "w036"
down_revision = "w035"


def upgrade() -> None:
    op.add_column(
        "write_fact_sources",
        sa.Column("access_groups", postgresql.ARRAY(sa.String(500)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("write_fact_sources", "access_groups")
