"""Add URI index to write_raw_sources for dedup lookups.

Revision ID: w031
Revises: w030
Create Date: 2026-03-30
"""

from alembic import op

revision = "w031"
down_revision = "w030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_write_raw_sources_uri",
        "write_raw_sources",
        ["uri"],
    )


def downgrade() -> None:
    op.drop_index("ix_write_raw_sources_uri", table_name="write_raw_sources")
