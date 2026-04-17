"""create plugin hybrid extractor schema

Revision ID: 0bb9768edc89
Revises:
Create Date: 2026-04-15 10:48:33.152514

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision: str = "0bb9768edc89"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "plugin_hybrid_extractor"


def upgrade() -> None:
    """Create plugin_hybrid_extractor schema and shell_candidates table."""
    # Schema is also created in env.py before migrations run (idempotent).
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.create_table(
        "shell_candidates",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("ner_label", sa.Text, nullable=True),
        sa.Column("source", sa.Text, nullable=False),  # "ner" | "chunk"
        sa.Column(
            "fact_ids",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scope", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=_SCHEMA,
    )

    op.create_index(
        "ix_shell_scope_created",
        "shell_candidates",
        ["scope", "created_at"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    """Drop shell_candidates table and plugin_hybrid_extractor schema."""
    op.drop_index("ix_shell_scope_created", table_name="shell_candidates", schema=_SCHEMA)
    op.drop_table("shell_candidates", schema=_SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
