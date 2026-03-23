"""Add write_page_fetch_log table.

Revision ID: w028
Revises: w027
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "w028"
down_revision = "w027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_page_fetch_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("raw_source_id", UUID(as_uuid=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fact_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("skip_reason", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_write_page_fetch_log_url", "write_page_fetch_log", ["url"], unique=True)
    op.create_index("ix_write_page_fetch_log_updated_at", "write_page_fetch_log", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_write_page_fetch_log_updated_at", table_name="write_page_fetch_log")
    op.drop_index("ix_write_page_fetch_log_url", table_name="write_page_fetch_log")
    op.drop_table("write_page_fetch_log")
