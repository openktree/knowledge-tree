"""add summary_data, workflow_run_id to research_reports; make FKs nullable

Revision ID: a1b2c3d4e5f6
Revises: v8w9x0y1z2a3
Create Date: 2026-04-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "v8w9x0y1z2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column("research_reports", sa.Column("workflow_run_id", sa.String(255), nullable=True))
    op.add_column("research_reports", sa.Column("summary_data", JSONB, nullable=True))
    op.create_index("ix_research_reports_workflow_run_id", "research_reports", ["workflow_run_id"])

    # Make FKs nullable (start decoupling from conversations)
    op.alter_column("research_reports", "message_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column("research_reports", "conversation_id", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    op.alter_column("research_reports", "conversation_id", existing_type=sa.UUID(), nullable=False)
    op.alter_column("research_reports", "message_id", existing_type=sa.UUID(), nullable=False)

    op.drop_index("ix_research_reports_workflow_run_id", table_name="research_reports")
    op.drop_column("research_reports", "summary_data")
    op.drop_column("research_reports", "workflow_run_id")
