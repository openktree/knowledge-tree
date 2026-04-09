"""writefacts dedup_status

Adds ``dedup_status`` column to ``write_facts`` (values: ``pending``,
``in_progress``, ``ready``). Existing rows are backfilled to ``ready``
because they have already been through the legacy insert-time dedup
path; the one-shot repair script takes care of any residual duplicates.

A partial index over non-ready rows keeps the snapshot query cheap.

Revision ID: e8909148c815
Revises: 777cdf5ff9e5
Create Date: 2026-04-08 21:36:51.415610

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8909148c815"
down_revision: Union[str, None] = "777cdf5ff9e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column with a server-side default so existing rows get a value.
    op.add_column(
        "write_facts",
        sa.Column(
            "dedup_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )

    # 2. Backfill every pre-existing row to 'ready' — legacy rows already
    #    passed the insert-time dedup path.
    op.execute("UPDATE write_facts SET dedup_status = 'ready'")

    # 3. Partial index for the dedup worker's snapshot scan.
    op.create_index(
        "ix_write_facts_dedup_pending",
        "write_facts",
        ["created_at"],
        postgresql_where=sa.text("dedup_status IN ('pending', 'in_progress')"),
    )


def downgrade() -> None:
    op.drop_index("ix_write_facts_dedup_pending", table_name="write_facts")
    op.drop_column("write_facts", "dedup_status")
