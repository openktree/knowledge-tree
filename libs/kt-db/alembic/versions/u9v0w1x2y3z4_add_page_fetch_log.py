"""add page_fetch_log table

Revision ID: u9v0w1x2y3z4
Revises: t8u9v0w1x2y3
Create Date: 2026-03-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u9v0w1x2y3z4"
down_revision = "v8w9x0y1z2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_fetch_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("url", sa.String(2000), nullable=False, unique=True, index=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column(
            "raw_source_id",
            sa.UUID(),
            sa.ForeignKey("raw_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skip_reason", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("page_fetch_log")
