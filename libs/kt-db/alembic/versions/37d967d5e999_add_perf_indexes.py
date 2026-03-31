"""Add performance indexes for wiki/MCP query optimization.

Adds:
- Partial index on nodes.parent_id (WHERE NOT NULL)
- Trigram GIN indexes on facts.content, fact_sources.author_org, raw_sources.uri
- Composite partial index on nodes(node_type, visibility) for synthesis listing

Revision ID: 37d967d5e999
Revises: zzag
Create Date: 2026-03-30
"""

from alembic import op

revision = "37d967d5e999"
down_revision = "9d8f11ec1631"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Parent lookup / child count queries
    op.execute("CREATE INDEX ix_nodes_parent_id ON nodes (parent_id) WHERE parent_id IS NOT NULL")

    # Trigram indexes for text search (pg_trgm already enabled)
    op.execute("CREATE INDEX ix_facts_content_trgm ON facts USING gin (content gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_fact_sources_author_org_trgm ON fact_sources "
        "USING gin (author_org gin_trgm_ops) WHERE author_org IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_raw_sources_uri_trgm ON raw_sources USING gin (uri gin_trgm_ops)")

    # Synthesis listing: frequently filtered by type + visibility
    op.execute(
        "CREATE INDEX ix_nodes_synth_visibility ON nodes (node_type, visibility, created_at DESC) "
        "WHERE node_type IN ('synthesis', 'supersynthesis')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_nodes_synth_visibility")
    op.execute("DROP INDEX IF EXISTS ix_raw_sources_uri_trgm")
    op.execute("DROP INDEX IF EXISTS ix_fact_sources_author_org_trgm")
    op.execute("DROP INDEX IF EXISTS ix_facts_content_trgm")
    op.execute("DROP INDEX IF EXISTS ix_nodes_parent_id")
