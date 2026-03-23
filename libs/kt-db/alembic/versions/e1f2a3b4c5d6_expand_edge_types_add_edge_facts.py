"""Expand edge types, add edge_facts junction table, add edge justification.

Data migration: positive -> related_to, negative -> contradicts.

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-02-21 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add justification column to edges
    op.add_column("edges", sa.Column("justification", sa.Text(), nullable=True))

    # Create edge_facts junction table
    op.create_table(
        "edge_facts",
        sa.Column("edge_id", sa.UUID(as_uuid=True), sa.ForeignKey("edges.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fact_id", sa.UUID(as_uuid=True), sa.ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relevance_score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("linked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("edge_id", "fact_id", name="uq_edge_fact"),
    )

    # Data migration: positive -> related_to, negative -> contradicts
    op.execute("UPDATE edges SET relationship_type = 'related_to' WHERE relationship_type = 'positive'")
    op.execute("UPDATE edges SET relationship_type = 'contradicts' WHERE relationship_type = 'negative'")


def downgrade() -> None:
    # Reverse data migration
    op.execute("UPDATE edges SET relationship_type = 'positive' WHERE relationship_type = 'related_to'")
    op.execute("UPDATE edges SET relationship_type = 'negative' WHERE relationship_type = 'contradicts'")

    # Drop edge_facts table
    op.drop_table("edge_facts")

    # Remove justification column
    op.drop_column("edges", "justification")
