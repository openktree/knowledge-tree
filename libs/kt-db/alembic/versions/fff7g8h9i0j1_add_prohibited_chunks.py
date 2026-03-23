"""Add prohibited_chunks table and prohibited_chunk_count to raw_sources.

Revision ID: fff7g8h9i0j1
Revises: eee6f7g8h9i0
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fff7g8h9i0j1"
down_revision = "eee6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prohibited_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "raw_source_id", UUID(as_uuid=True), sa.ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("fallback_model_id", sa.String(200), nullable=True),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_prohibited_chunks_raw_source_id",
        "prohibited_chunks",
        ["raw_source_id"],
    )
    op.add_column(
        "raw_sources",
        sa.Column("prohibited_chunk_count", sa.Integer, server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "prohibited_chunk_count")
    op.drop_table("prohibited_chunks")
