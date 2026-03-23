"""Add stream_events table for persisting Redis Stream worker events.

Revision ID: n2b3c4d5e6f7
Revises: m1a2b3c4d5e6
Create Date: 2026-03-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "n2b3c4d5e6f7"
down_revision = "m1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream_name", sa.String(100), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("worker_name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_stream_events_message_id", "stream_events", ["message_id"])
    op.create_index("ix_stream_events_message_stream", "stream_events", ["message_id", "stream_name"])
    op.create_index("ix_stream_events_message_created", "stream_events", ["message_id", "created_at"])


def downgrade() -> None:
    op.drop_table("stream_events")
