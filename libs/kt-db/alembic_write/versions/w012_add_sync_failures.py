"""add sync_failures dead-letter table

Revision ID: w012
Revises: w011
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "w012"
down_revision = "w011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_failures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("record_key", sa.String(1200), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("retry_count", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("next_retry_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_sync_failures_next_retry_at", "sync_failures", ["next_retry_at"])
    op.create_index("ix_sync_failures_status", "sync_failures", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sync_failures_status")
    op.drop_index("ix_sync_failures_next_retry_at")
    op.drop_table("sync_failures")
