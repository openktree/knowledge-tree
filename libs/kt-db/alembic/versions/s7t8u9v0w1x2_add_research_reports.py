"""add research_reports table

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-03-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "s7t8u9v0w1x2"
down_revision = "r6s7t8u9v0w1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nodes_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("edges_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("waves_completed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("explore_budget", sa.Integer, nullable=True),
        sa.Column("explore_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("nav_budget", sa.Integer, nullable=True),
        sa.Column("nav_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scope_summaries", ARRAY(sa.Text), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_research_reports_conversation_id", "research_reports", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_research_reports_conversation_id", "research_reports")
    op.drop_table("research_reports")
