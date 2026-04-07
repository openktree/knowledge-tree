"""Add graph_groups, graph_group_members tables and access_groups on raw_sources.

Supports per-graph group-based source-level access control.
Groups are scoped to each graph schema (no cross-org collision).
access_groups on raw_sources: NULL/empty = public, non-empty = restricted.

Revision ID: f982135a0b7f
Revises: f6da7767d71f
Create Date: 2026-04-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f982135a0b7f"
down_revision = "f6da7767d71f"


def upgrade() -> None:
    # graph_groups table
    op.create_table(
        "graph_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # graph_group_members table
    op.create_table(
        "graph_group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["group_id"], ["graph_groups.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_graph_group_member"),
    )
    op.create_index("ix_graph_group_members_group_id", "graph_group_members", ["group_id"])
    op.create_index("ix_graph_group_members_user_id", "graph_group_members", ["user_id"])

    # access_groups column on raw_sources
    op.add_column(
        "raw_sources",
        sa.Column("access_groups", postgresql.ARRAY(sa.String(500)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "access_groups")
    op.drop_table("graph_group_members")
    op.drop_table("graph_groups")
