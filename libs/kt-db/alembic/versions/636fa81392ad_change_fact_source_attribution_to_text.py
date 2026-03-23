"""change_fact_source_attribution_to_text

Revision ID: 636fa81392ad
Revises: t8u9v0w1x2y3
Create Date: 2026-03-06 11:34:36.027317

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "636fa81392ad"
down_revision: Union[str, Sequence[str], None] = "t8u9v0w1x2y3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "fact_sources", "attribution", existing_type=sa.VARCHAR(length=500), type_=sa.Text(), existing_nullable=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "fact_sources", "attribution", existing_type=sa.Text(), type_=sa.VARCHAR(length=500), existing_nullable=True
    )
