"""Add missing FK indexes for concurrent write performance.

Every FK column without an index causes seq scans during JOINs and CASCADE
deletes.  This migration adds indexes on all FK columns that were missing one.

Revision ID: k9f0a1b2c3d4
Revises: j8e9f0a1b2c3
Create Date: 2026-02-27
"""

from alembic import op

revision = "k9f0a1b2c3d4"
down_revision = "j8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # edges FK columns
    op.create_index("ix_edges_source_node_id", "edges", ["source_node_id"])
    op.create_index("ix_edges_target_node_id", "edges", ["target_node_id"])

    # dimensions FK column
    op.create_index("ix_dimensions_node_id", "dimensions", ["node_id"])

    # node_facts -- node_id is PK part so indexed; fact_id needs index
    op.create_index("ix_node_facts_fact_id", "node_facts", ["fact_id"])

    # edge_facts -- edge_id is PK part so indexed; fact_id needs index
    op.create_index("ix_edge_facts_fact_id", "edge_facts", ["fact_id"])

    # fact_sources -- both FK columns need indexes
    op.create_index("ix_fact_sources_fact_id", "fact_sources", ["fact_id"])
    op.create_index("ix_fact_sources_raw_source_id", "fact_sources", ["raw_source_id"])

    # dimension_facts -- dimension_id is PK part so indexed; fact_id needs index
    op.create_index("ix_dimension_facts_fact_id", "dimension_facts", ["fact_id"])

    # node_versions FK column
    op.create_index("ix_node_versions_node_id", "node_versions", ["node_id"])

    # node_fact_rejections -- node_id already has ix_node_fact_rejections_node_id; fact_id needs index
    op.create_index("ix_node_fact_rejections_fact_id", "node_fact_rejections", ["fact_id"])

    # conversation_messages FK column
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])

    # fact_edge_evaluations -- source/target already indexed; fact_id needs index
    op.create_index("ix_fact_edge_evaluations_fact_id", "fact_edge_evaluations", ["fact_id"])


def downgrade() -> None:
    op.drop_index("ix_fact_edge_evaluations_fact_id")
    op.drop_index("ix_conversation_messages_conversation_id")
    op.drop_index("ix_node_fact_rejections_fact_id")
    op.drop_index("ix_node_versions_node_id")
    op.drop_index("ix_dimension_facts_fact_id")
    op.drop_index("ix_fact_sources_raw_source_id")
    op.drop_index("ix_fact_sources_fact_id")
    op.drop_index("ix_edge_facts_fact_id")
    op.drop_index("ix_node_facts_fact_id")
    op.drop_index("ix_dimensions_node_id")
    op.drop_index("ix_edges_target_node_id")
    op.drop_index("ix_edges_source_node_id")
