"""Add event_log table for unified event persistence (outbox pattern).

Revision ID: o3c4d5e6f7g8
Revises: n2b3c4d5e6f7
Create Date: 2026-03-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "o3c4d5e6f7g8"
down_revision = "n2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=True),
        sa.Column("stream_name", sa.Text, nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_event_log_msg_seq", "event_log", ["message_id", "id"])
    op.create_index(
        "ix_event_log_msg_scope",
        "event_log",
        ["message_id", "scope_id"],
        postgresql_where=sa.text("scope_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("event_log")
