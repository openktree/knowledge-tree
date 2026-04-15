"""add_aliases_to_seeds

Revision ID: 10d0aab50d53
Revises: 5b16c2127652
Create Date: 2026-04-15 08:31:25.565489

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '10d0aab50d53'
down_revision: Union[str, None] = '5b16c2127652'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "write_seeds",
        sa.Column(
            "aliases",
            sa.ARRAY(sa.String(500)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index(
        "ix_write_seeds_aliases",
        "write_seeds",
        ["aliases"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_write_seeds_aliases", table_name="write_seeds")
    op.drop_column("write_seeds", "aliases")
