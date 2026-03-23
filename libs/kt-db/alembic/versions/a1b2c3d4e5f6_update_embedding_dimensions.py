"""update embedding dimensions to 4096

Revision ID: a1b2c3d4e5f6
Revises: 3256dd2096fd
Create Date: 2026-02-16 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "3256dd2096fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade embedding columns from Vector(1536) to Vector(4096)."""
    op.execute("ALTER TABLE nodes ALTER COLUMN embedding TYPE vector(4096)")
    op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(4096)")
    op.execute("ALTER TABLE dimensions ALTER COLUMN embedding TYPE vector(4096)")


def downgrade() -> None:
    """Downgrade embedding columns from Vector(4096) to Vector(1536)."""
    op.execute("ALTER TABLE dimensions ALTER COLUMN embedding TYPE vector(1536)")
    op.execute("ALTER TABLE facts ALTER COLUMN embedding TYPE vector(1536)")
    op.execute("ALTER TABLE nodes ALTER COLUMN embedding TYPE vector(1536)")
