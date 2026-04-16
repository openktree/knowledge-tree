"""shell_candidates unique constraint + reason column

Revision ID: bbf79468c84d
Revises: 0bb9768edc89
Create Date: 2026-04-15 18:07:45.952132

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bbf79468c84d"
down_revision: Union[str, Sequence[str], None] = "0bb9768edc89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "plugin_hybrid_extractor"


def upgrade() -> None:
    """Add ``reason`` column and a unique constraint on (scope, name, source).

    Enables deterministic upserts: the extractor derives each shell's PK
    from uuid5(scope|name|source) and relies on ON CONFLICT DO NOTHING
    against this unique constraint when re-ingesting the same scope.
    """
    op.add_column(
        "shell_candidates",
        sa.Column("reason", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_shell_scope_name_source",
        "shell_candidates",
        ["scope", "name", "source"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_shell_scope_name_source",
        "shell_candidates",
        schema=_SCHEMA,
        type_="unique",
    )
    op.drop_column("shell_candidates", "reason", schema=_SCHEMA)
