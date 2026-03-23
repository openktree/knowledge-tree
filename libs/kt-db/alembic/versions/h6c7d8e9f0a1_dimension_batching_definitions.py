"""Dimension batching, saturation, definitions, and fact rejection.

Adds:
- dimensions: batch_index, fact_count, is_definitive columns
- dimension_facts: join table tracking which facts produced a dimension
- node_fact_rejections: persistent exclusion of rejected facts per node
- nodes: definition, definition_generated_at columns

Revision ID: h6c7d8e9f0a1
Revises: g5b6c7d8e9f0
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "h6c7d8e9f0a1"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Dimension columns --
    op.add_column("dimensions", sa.Column("batch_index", sa.Integer(), server_default="0", nullable=False))
    op.add_column("dimensions", sa.Column("fact_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("dimensions", sa.Column("is_definitive", sa.Boolean(), server_default="false", nullable=False))

    # -- Node definition columns --
    op.add_column("nodes", sa.Column("definition", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("definition_generated_at", sa.DateTime(), nullable=True))

    # -- DimensionFact join table --
    op.create_table(
        "dimension_facts",
        sa.Column("dimension_id", UUID(as_uuid=True), sa.ForeignKey("dimensions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fact_id", UUID(as_uuid=True), sa.ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("linked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dimension_id", "fact_id", name="uq_dimension_fact"),
    )

    # -- NodeFactRejection table --
    op.create_table(
        "node_fact_rejections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_id", UUID(as_uuid=True), sa.ForeignKey("facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rejected_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("node_id", "fact_id", name="uq_node_fact_rejection"),
    )
    op.create_index("ix_node_fact_rejections_node_id", "node_fact_rejections", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_node_fact_rejections_node_id", table_name="node_fact_rejections")
    op.drop_table("node_fact_rejections")
    op.drop_table("dimension_facts")
    op.drop_column("nodes", "definition_generated_at")
    op.drop_column("nodes", "definition")
    op.drop_column("dimensions", "is_definitive")
    op.drop_column("dimensions", "fact_count")
    op.drop_column("dimensions", "batch_index")
