"""Add workflow_run_id to conversation_messages.

Revision ID: p4d5e6f7g8h9
Revises: o3c4d5e6f7g8
Create Date: 2026-03-03
"""

import sqlalchemy as sa
from alembic import op

revision = "p4d5e6f7g8h9"
down_revision = "o3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("workflow_run_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "workflow_run_id")
