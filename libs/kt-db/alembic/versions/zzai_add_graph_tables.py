"""Add multi-graph tables: database_connections, graphs, graph_members.

Seeds the default graph row so existing data continues to work.

Revision ID: zzai
Revises: zzah
Create Date: 2026-04-05
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "zzai"
down_revision = "zzah"
branch_labels = None
depends_on = None

# Fixed UUID for the default graph — deterministic so all environments match.
DEFAULT_GRAPH_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    # -- database_connections --
    op.create_table(
        "database_connections",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("config_key", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # -- graphs --
    op.create_table(
        "graphs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, server_default="false", nullable=False),
        sa.Column("graph_type", sa.String(20), server_default="v1", nullable=False),
        sa.Column("byok_enabled", sa.Boolean, server_default="false", nullable=False),
        sa.Column("storage_mode", sa.String(20), server_default="schema", nullable=False),
        sa.Column("schema_name", sa.String(100), nullable=False),
        sa.Column(
            "database_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), server_default="provisioning", nullable=False),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # -- graph_members --
    op.create_table(
        "graph_members",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "graph_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graphs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("graph_id", "user_id", name="uq_graph_member"),
    )

    # -- Seed default graph --
    op.execute(
        sa.text(
            """
            INSERT INTO graphs (id, slug, name, is_default, storage_mode, schema_name, status)
            VALUES (:id, 'default', 'Default Graph', true, 'schema', 'public', 'active')
            ON CONFLICT (slug) DO NOTHING
            """
        ).bindparams(id=DEFAULT_GRAPH_ID)
    )


def downgrade() -> None:
    op.drop_table("graph_members")
    op.drop_table("graphs")
    op.drop_table("database_connections")
