"""Update embedding columns from Vector(4096) to Vector(3072) for text-embedding-3-large.

Revision ID: j8e9f0a1b2c3
Revises: i7d8e9f0a1b2
Create Date: 2026-02-27
"""

from alembic import op

revision = "j8e9f0a1b2c3"
down_revision = "i7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE nodes ALTER COLUMN embedding TYPE vector(3072)")
    op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(3072)")
    op.execute("ALTER TABLE dimensions ALTER COLUMN embedding TYPE vector(3072)")


def downgrade() -> None:
    op.execute("ALTER TABLE dimensions ALTER COLUMN embedding TYPE vector(4096)")
    op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(4096)")
    op.execute("ALTER TABLE nodes ALTER COLUMN embedding TYPE vector(4096)")
