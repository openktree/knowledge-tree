"""Drop write_tentative_edge_facts and write_edge_justifications tables.

These tables are replaced by the candidate-based edge justification system
that uses write_edge_candidates directly.

Revision ID: w023
Revises: w022
"""

from alembic import op

revision = "w023"
down_revision = "w022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("write_edge_justifications")
    op.drop_table("write_tentative_edge_facts")


def downgrade() -> None:
    # Forward-only migration — tables are not recreated.
    pass
