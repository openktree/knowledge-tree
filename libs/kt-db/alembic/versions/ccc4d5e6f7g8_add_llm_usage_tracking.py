"""Add LLM usage tracking to research reports.

Revision ID: ccc4d5e6f7g8
Revises: bbb3c4d5e6f7
Create Date: 2026-03-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ccc4d5e6f7g8"
down_revision = "bbb3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add token/cost columns to research_reports
    op.add_column(
        "research_reports",
        sa.Column("total_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_reports",
        sa.Column("total_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_reports",
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
    )

    # Create per-model usage records table
    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_report_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index(
        "ix_llm_usage_records_research_report_id",
        "llm_usage_records",
        ["research_report_id"],
    )


def downgrade() -> None:
    op.drop_table("llm_usage_records")
    op.drop_column("research_reports", "total_cost_usd")
    op.drop_column("research_reports", "total_completion_tokens")
    op.drop_column("research_reports", "total_prompt_tokens")
