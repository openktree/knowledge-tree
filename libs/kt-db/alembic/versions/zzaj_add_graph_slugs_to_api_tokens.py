"""Add graph_slugs column to api_tokens for multi-graph scoping.

NULL = all graphs the user can access (backward compatible).
Non-null = restricted to listed graph slugs.

Control-plane table — only in public schema.

Revision ID: zzaj
Revises: zzai
Create Date: 2026-04-05
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "zzaj"
down_revision = "zzai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = os.environ.get("ALEMBIC_SCHEMA")
    if schema and schema != "public":
        return
    op.add_column(
        "api_tokens",
        sa.Column("graph_slugs", sa.ARRAY(sa.String), nullable=True),
    )


def downgrade() -> None:
    schema = os.environ.get("ALEMBIC_SCHEMA")
    if schema and schema != "public":
        return
    op.drop_column("api_tokens", "graph_slugs")
