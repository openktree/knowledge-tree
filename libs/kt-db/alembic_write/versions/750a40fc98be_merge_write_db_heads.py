"""merge write-db heads

Revision ID: 750a40fc98be
Revises: 3f7a2b289e43, e8909148c815
Create Date: 2026-04-12 14:17:19.095932

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "750a40fc98be"
down_revision: Union[str, None] = ("3f7a2b289e43", "e8909148c815")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
