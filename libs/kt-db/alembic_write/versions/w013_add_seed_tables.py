"""add seed tables for node seeds feature

Revision ID: w013
Revises: w012
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "w013"
down_revision = "w012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- write_seeds --
    op.create_table(
        "write_seeds",
        sa.Column("key", sa.String(500), primary_key=True),
        sa.Column("seed_uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("entity_subtype", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), server_default="'active'", nullable=False),
        sa.Column("merged_into_key", sa.String(500), nullable=True),
        sa.Column("promoted_node_key", sa.String(500), nullable=True),
        sa.Column("fact_count", sa.Integer, server_default="0"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_seeds_updated_at", "write_seeds", ["updated_at"])
    op.create_index("ix_write_seeds_status", "write_seeds", ["status"])
    op.create_index("ix_write_seeds_seed_uuid", "write_seeds", ["seed_uuid"], unique=True)
    op.create_index(
        "ix_write_seeds_name_trgm",
        "write_seeds",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    # -- write_seed_facts --
    op.create_table(
        "write_seed_facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("seed_key", sa.String(500), nullable=False),
        sa.Column("fact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column("extraction_context", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_seed_facts_updated_at", "write_seed_facts", ["updated_at"])
    op.create_index("ix_write_seed_facts_seed_key", "write_seed_facts", ["seed_key"])
    op.create_index("uq_wsf_seed_fact", "write_seed_facts", ["seed_key", "fact_id"], unique=True)

    # -- write_edge_candidates --
    op.create_table(
        "write_edge_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("seed_key_a", sa.String(500), nullable=False),
        sa.Column("seed_key_b", sa.String(500), nullable=False),
        sa.Column("co_occurring_fact_ids", ARRAY(sa.String), nullable=False),
        sa.Column("fact_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("evaluation_result", JSONB, nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_edge_candidates_updated_at", "write_edge_candidates", ["updated_at"])
    op.create_index("uq_wec_pair", "write_edge_candidates", ["seed_key_a", "seed_key_b"], unique=True)

    # -- write_seed_merges --
    op.create_table(
        "write_seed_merges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("source_seed_key", sa.String(500), nullable=False),
        sa.Column("target_seed_key", sa.String(500), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("fact_ids_moved", ARRAY(sa.String), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_write_seed_merges_updated_at", "write_seed_merges", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_write_seed_merges_updated_at")
    op.drop_table("write_seed_merges")
    op.drop_index("uq_wec_pair")
    op.drop_index("ix_write_edge_candidates_updated_at")
    op.drop_table("write_edge_candidates")
    op.drop_index("uq_wsf_seed_fact")
    op.drop_index("ix_write_seed_facts_seed_key")
    op.drop_index("ix_write_seed_facts_updated_at")
    op.drop_table("write_seed_facts")
    op.drop_index("ix_write_seeds_name_trgm")
    op.drop_index("ix_write_seeds_seed_uuid")
    op.drop_index("ix_write_seeds_status")
    op.drop_index("ix_write_seeds_updated_at")
    op.drop_table("write_seeds")
