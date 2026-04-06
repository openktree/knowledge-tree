"""Clear graph-db sources so sync rebuilds them with deterministic IDs.

Write-db sources have been re-IDed with deterministic UUIDs derived from
URI (migration w034). Graph-db sources still have old random UUIDs.
Deleting them lets the sync worker recreate them from write-db with
matching IDs.

CASCADE on fact_sources and prohibited_chunks FKs handles cleanup.

Revision ID: zzah
Revises: zzag
Create Date: 2026-04-02
"""

from alembic import op
from sqlalchemy import text

revision = "zzah"
down_revision = "zzag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Delete all sources — CASCADE deletes fact_sources and prohibited_chunks
    conn.execute(text("DELETE FROM raw_sources"))


def downgrade() -> None:
    # Sync worker will have rebuilt the data; cannot reverse deletion.
    pass
