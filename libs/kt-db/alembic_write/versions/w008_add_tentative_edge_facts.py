"""Add tentative edge facts and edge justifications tables.

Revision ID: w008
Revises: w007
Create Date: 2026-03-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "w008"
down_revision = "w007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_tentative_edge_facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_node_key", sa.String(500), nullable=False),
        sa.Column("target_node_key", sa.String(500), nullable=False),
        sa.Column("fact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="tentative"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_wtef_updated_at", "write_tentative_edge_facts", ["updated_at"])
    op.create_index("ix_wtef_pair", "write_tentative_edge_facts", ["source_node_key", "target_node_key"])
    op.create_index(
        "uq_wtef",
        "write_tentative_edge_facts",
        ["source_node_key", "target_node_key", "fact_id"],
        unique=True,
    )

    op.create_table(
        "write_edge_justifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("edge_key", sa.String(1200), nullable=False),
        sa.Column("justification", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("fact_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_draft", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("fact_ids", ARRAY(sa.String), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_wej_edge_key", "write_edge_justifications", ["edge_key"])
    op.create_index("ix_wej_updated_at", "write_edge_justifications", ["updated_at"])


def downgrade() -> None:
    op.drop_table("write_edge_justifications")
    op.drop_table("write_tentative_edge_facts")
