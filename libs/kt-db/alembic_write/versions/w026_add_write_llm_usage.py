"""Add write_llm_usage table for flat per-task usage tracking.

Revision ID: w026
Revises: w025
Create Date: 2026-03-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "w026"
down_revision = "w025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_llm_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("workflow_run_id", sa.String(100), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_write_llm_usage_updated_at", "write_llm_usage", ["updated_at"])
    op.create_index("ix_write_llm_usage_conversation_id", "write_llm_usage", ["conversation_id"])
    op.create_index("ix_write_llm_usage_message_id", "write_llm_usage", ["message_id"])
    op.create_index("ix_write_llm_usage_task_type", "write_llm_usage", ["task_type"])


def downgrade() -> None:
    op.drop_table("write_llm_usage")
