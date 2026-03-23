"""add GIN trigram index on write_nodes.concept

Enables pg_trgm similarity searches on write-db so workers can do
node dedup checks without hitting the graph-db connection pool.

Revision ID: w006
Revises: w005
Create Date: 2026-03-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "w006"
down_revision: Union[str, None] = "w005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_write_nodes_concept_trgm "
        "ON write_nodes USING gin (concept gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_write_nodes_concept_trgm")
