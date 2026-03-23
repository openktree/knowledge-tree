"""Add write_prohibited_chunks table and prohibited_chunk_count to write_raw_sources.

Revision ID: w024
Revises: w023
Create Date: 2026-03-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "w024"
down_revision = "w023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_prohibited_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("fallback_model_id", sa.String(200), nullable=True),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("fallback_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_write_prohibited_chunks_updated_at",
        "write_prohibited_chunks",
        ["updated_at"],
    )
    op.create_index(
        "ix_write_prohibited_chunks_content_hash",
        "write_prohibited_chunks",
        ["source_content_hash"],
    )
    op.add_column(
        "write_raw_sources",
        sa.Column("prohibited_chunk_count", sa.Integer, server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("write_raw_sources", "prohibited_chunk_count")
    op.drop_table("write_prohibited_chunks")
