"""merge migration heads

Revision ID: fcd2d573ed5e
Revises: 14c8ed3f9b77, zzah
Create Date: 2026-04-03 12:16:39.381694

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "fcd2d573ed5e"
down_revision: Union[str, Sequence[str], None] = ("14c8ed3f9b77", "zzah")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
