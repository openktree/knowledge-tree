"""initial write-db schema

Revision ID: w001
Revises:
Create Date: 2026-03-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "w001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # write_nodes
    op.create_table(
        "write_nodes",
        sa.Column("key", sa.String(500), primary_key=True),
        sa.Column("node_uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("concept", sa.String(500), nullable=False),
        sa.Column("node_type", sa.String(20), nullable=False, server_default="concept"),
        sa.Column("parent_key", sa.String(500), nullable=True),
        sa.Column("source_concept_key", sa.String(500), nullable=True),
        sa.Column("definition", sa.Text, nullable=True),
        sa.Column("definition_source", sa.String(20), nullable=True),
        sa.Column("attractor", sa.String(500), nullable=True),
        sa.Column("filter_id", sa.String(100), nullable=True),
        sa.Column("max_content_tokens", sa.Integer, server_default="500"),
        sa.Column("stale_after", sa.Integer, server_default="30"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_nodes_updated_at", "write_nodes", ["updated_at"])
    op.create_index("ix_write_nodes_node_type", "write_nodes", ["node_type"])
    op.create_index("ix_write_nodes_node_uuid", "write_nodes", ["node_uuid"], unique=True)

    # write_edges
    op.create_table(
        "write_edges",
        sa.Column("key", sa.String(1200), primary_key=True),
        sa.Column("source_node_key", sa.String(500), nullable=False),
        sa.Column("target_node_key", sa.String(500), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False),
        sa.Column("weight", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_edges_updated_at", "write_edges", ["updated_at"])
    op.create_index("ix_write_edges_source_key", "write_edges", ["source_node_key"])
    op.create_index("ix_write_edges_target_key", "write_edges", ["target_node_key"])

    # write_dimensions
    op.create_table(
        "write_dimensions",
        sa.Column("key", sa.String(800), primary_key=True),
        sa.Column("node_key", sa.String(500), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, server_default="0.0"),
        sa.Column("suggested_concepts", sa.ARRAY(sa.String), nullable=True),
        sa.Column("batch_index", sa.Integer, server_default="0"),
        sa.Column("fact_count", sa.Integer, server_default="0"),
        sa.Column("is_definitive", sa.Boolean, server_default="false"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_dimensions_updated_at", "write_dimensions", ["updated_at"])
    op.create_index("ix_write_dimensions_node_key", "write_dimensions", ["node_key"])

    # write_convergence_reports
    op.create_table(
        "write_convergence_reports",
        sa.Column("node_key", sa.String(500), primary_key=True),
        sa.Column("convergence_score", sa.Float, server_default="0.0"),
        sa.Column("converged_claims", sa.ARRAY(sa.String), nullable=True),
        sa.Column("recommended_content", sa.Text, nullable=True),
        sa.Column("computed_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_convergence_updated_at", "write_convergence_reports", ["updated_at"])

    # write_divergent_claims
    op.create_table(
        "write_divergent_claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_key", sa.String(500), nullable=False),
        sa.Column("claim", sa.Text, nullable=False),
        sa.Column("model_positions", JSONB, nullable=True),
        sa.Column("divergence_type", sa.String(100), nullable=True),
        sa.Column("analysis", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_divergent_claims_node_key", "write_divergent_claims", ["node_key"])
    op.create_index("ix_write_divergent_claims_updated_at", "write_divergent_claims", ["updated_at"])

    # write_node_counters
    op.create_table(
        "write_node_counters",
        sa.Column("node_key", sa.String(500), primary_key=True),
        sa.Column("access_count", sa.Integer, server_default="0"),
        sa.Column("update_count", sa.Integer, server_default="0"),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_node_counters_updated_at", "write_node_counters", ["updated_at"])

    # sync_watermarks
    op.create_table(
        "sync_watermarks",
        sa.Column("table_name", sa.String(100), primary_key=True),
        sa.Column("last_synced_at", sa.DateTime, nullable=False, server_default=sa.text("'1970-01-01'")),
    )


def downgrade() -> None:
    op.drop_table("sync_watermarks")
    op.drop_table("write_node_counters")
    op.drop_table("write_divergent_claims")
    op.drop_table("write_convergence_reports")
    op.drop_table("write_dimensions")
    op.drop_table("write_edges")
    op.drop_table("write_nodes")
