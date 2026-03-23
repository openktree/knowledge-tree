"""Add pipeline_scopes and pipeline_events tables for persistent pipeline tracking.

Revision ID: m1a2b3c4d5e6
Revises: l0a1b2c3d4e5
Create Date: 2026-02-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "m1a2b3c4d5e6"
down_revision = "l0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_scopes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.String(200), nullable=False),
        sa.Column("scope_name", sa.String(500), nullable=False),
        sa.Column("wave_number", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("node_count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("message_id", "scope_id", name="uq_pipeline_scope_msg_scope"),
    )
    op.create_index("ix_pipeline_scopes_message_id", "pipeline_scopes", ["message_id"])

    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "scope_row_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pipeline_scopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("phase", sa.String(30), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=True),
        sa.Column("tool_params", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_pipeline_events_scope_created",
        "pipeline_events",
        ["scope_row_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("pipeline_events")
    op.drop_table("pipeline_scopes")
