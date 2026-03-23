"""Add discovery_strategy column to write_tentative_edge_facts and write_edge_candidates.

Tracks which edge discovery strategy (seed_cooccurrence, text_search,
embedding_search) found each fact for a candidate pair.

Revision ID: w020
Revises: w019
"""

import sqlalchemy as sa
from alembic import op

revision = "w020"
down_revision = "w019"


def upgrade() -> None:
    op.add_column(
        "write_tentative_edge_facts",
        sa.Column("discovery_strategy", sa.String(50), nullable=True),
    )
    op.add_column(
        "write_edge_candidates",
        sa.Column("discovery_strategy", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("write_edge_candidates", "discovery_strategy")
    op.drop_column("write_tentative_edge_facts", "discovery_strategy")
