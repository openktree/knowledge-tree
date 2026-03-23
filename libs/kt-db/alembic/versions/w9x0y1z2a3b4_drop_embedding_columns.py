"""drop embedding columns — vector search moved to Qdrant

Revision ID: w9x0y1z2a3b4
Revises: v8w9x0y1z2a3
Create Date: 2026-03-09 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w9x0y1z2a3b4"
down_revision: Union[str, Sequence[str], None] = "v8w9x0y1z2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("nodes", "embedding")
    op.drop_column("facts", "embedding")
    op.drop_column("dimensions", "embedding")


def downgrade() -> None:
    # Re-add as nullable vector(3072) columns using raw SQL
    op.execute("ALTER TABLE nodes ADD COLUMN embedding vector(3072)")
    op.execute("ALTER TABLE facts ADD COLUMN embedding vector(3072)")
    op.execute("ALTER TABLE dimensions ADD COLUMN embedding vector(3072)")
