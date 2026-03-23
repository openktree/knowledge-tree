"""drop event_log table

Revision ID: q5e6f7g8h9i0
Revises: p4d5e6f7g8h9
Create Date: 2026-03-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "q5e6f7g8h9i0"
down_revision = "p4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("event_log")


def downgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("stream_name", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["conversation_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
