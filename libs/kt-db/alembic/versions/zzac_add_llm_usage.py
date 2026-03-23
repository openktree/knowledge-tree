"""Add llm_usage table for flat per-task usage tracking.

Revision ID: zzac
Revises: zzab
Create Date: 2026-03-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "zzac"
down_revision = "zzab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("workflow_run_id", sa.String(100), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_llm_usage_conversation_id", "llm_usage", ["conversation_id"])
    op.create_index("ix_llm_usage_message_id", "llm_usage", ["message_id"])
    op.create_index("ix_llm_usage_task_type", "llm_usage", ["task_type"])
    op.create_index("ix_llm_usage_model_id", "llm_usage", ["model_id"])
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_usage")
