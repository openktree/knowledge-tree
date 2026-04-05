"""Add graph_slugs column to api_tokens for multi-graph scoping.

NULL = all graphs the user can access (backward compatible).
Non-null = restricted to listed graph slugs.

Revision ID: zzaj
Revises: zzai
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op

revision = "zzaj"
down_revision = "zzai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column("graph_slugs", sa.ARRAY(sa.String), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_tokens", "graph_slugs")
