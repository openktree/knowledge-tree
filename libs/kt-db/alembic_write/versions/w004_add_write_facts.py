"""add write_facts and related tables

Revision ID: w004
Revises: w003
Create Date: 2026-03-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "w004"
down_revision: Union[str, None] = "w003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # write_facts
    op.create_table(
        "write_facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("fact_type", sa.String(50), nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_facts_updated_at", "write_facts", ["updated_at"])
    op.create_index("ix_write_facts_fact_type", "write_facts", ["fact_type"])

    # write_fact_sources
    op.create_table(
        "write_fact_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("fact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("raw_source_uri", sa.String(2000), nullable=False),
        sa.Column("raw_source_title", sa.String(1000), nullable=True),
        sa.Column("raw_source_content_hash", sa.String(64), nullable=False),
        sa.Column("raw_source_provider_id", sa.String(100), nullable=False),
        sa.Column("context_snippet", sa.Text, nullable=True),
        sa.Column("attribution", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_fact_sources_updated_at", "write_fact_sources", ["updated_at"])
    op.create_index("ix_write_fact_sources_fact_id", "write_fact_sources", ["fact_id"])

    # write_node_fact_rejections
    op.create_table(
        "write_node_fact_rejections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_nfr_updated_at", "write_node_fact_rejections", ["updated_at"])
    op.create_index("ix_write_nfr_node_id", "write_node_fact_rejections", ["node_id"])
    op.create_index(
        "uq_write_nfr_node_fact",
        "write_node_fact_rejections",
        ["node_id", "fact_id"],
        unique=True,
    )

    # write_fact_edge_evaluations
    op.create_table(
        "write_fact_edge_evaluations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_fee_updated_at", "write_fact_edge_evaluations", ["updated_at"])
    op.create_index(
        "ix_write_fee_source_target",
        "write_fact_edge_evaluations",
        ["source_node_id", "target_node_id"],
    )
    op.create_index(
        "uq_write_fee",
        "write_fact_edge_evaluations",
        ["source_node_id", "target_node_id", "fact_id"],
        unique=True,
    )

    # GIN index on write_nodes.fact_ids for array overlap queries
    op.execute("CREATE INDEX IF NOT EXISTS ix_write_nodes_fact_ids_gin ON write_nodes USING GIN (fact_ids)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_write_nodes_fact_ids_gin")
    op.drop_table("write_fact_edge_evaluations")
    op.drop_table("write_node_fact_rejections")
    op.drop_table("write_fact_sources")
    op.drop_table("write_facts")
