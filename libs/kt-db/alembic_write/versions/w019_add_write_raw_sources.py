"""Add write_raw_sources table.

Workers write raw sources here during search, and read them back during
decomposition — avoiding graph-db connection pool pressure when many
decompose tasks run concurrently.

Revision ID: w019
Revises: w018
"""

revision = "w019"
down_revision = "w018"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade() -> None:
    op.create_table(
        "write_raw_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("uri", sa.String(2000), nullable=False),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("raw_content", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_full_text", sa.Boolean, server_default="false"),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("provider_metadata", JSONB, nullable=True),
        sa.Column("fact_count", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_write_raw_sources_updated_at",
        "write_raw_sources",
        ["updated_at"],
    )
    op.create_index(
        "ix_write_raw_sources_content_hash",
        "write_raw_sources",
        ["content_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("write_raw_sources")
