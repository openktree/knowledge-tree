"""Add facts_at_last_build column to write_nodes.

Tracks how many facts a node had when dimensions were last generated.
Used for fact-staleness detection in auto_build_graph.

Revision ID: w029
Revises: w028
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa

revision = "w029"
down_revision = "w028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_nodes",
        sa.Column("facts_at_last_build", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("write_nodes", "facts_at_last_build")
