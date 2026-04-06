"""Add graph_slug column to sync_watermarks and sync_failures.

Supports multi-graph sync by scoping watermarks per graph.
Existing rows get graph_slug='default'.

Revision ID: w035
Revises: w034
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op

revision = "w035"
down_revision = "w034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- sync_watermarks: drop old PK, add graph_slug, create composite PK --
    op.drop_constraint("sync_watermarks_pkey", "sync_watermarks", type_="primary")
    op.add_column(
        "sync_watermarks",
        sa.Column("graph_slug", sa.String(100), nullable=False, server_default="default"),
    )
    op.create_primary_key("sync_watermarks_pkey", "sync_watermarks", ["table_name", "graph_slug"])

    # -- sync_failures: add graph_slug column --
    op.add_column(
        "sync_failures",
        sa.Column("graph_slug", sa.String(100), nullable=False, server_default="default"),
    )


def downgrade() -> None:
    op.drop_column("sync_failures", "graph_slug")

    op.drop_constraint("sync_watermarks_pkey", "sync_watermarks", type_="primary")
    op.drop_column("sync_watermarks", "graph_slug")
    op.create_primary_key("sync_watermarks_pkey", "sync_watermarks", ["table_name"])
