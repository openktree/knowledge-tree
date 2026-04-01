"""Re-ID write_raw_sources with deterministic UUIDs derived from URI.

Ensures write-db and graph-db always agree on source IDs for the same URL.
Also resets sync watermarks so the sync worker rebuilds graph-db sources
with the new deterministic IDs.

Revision ID: w034
Revises: w033
Create Date: 2026-04-02
"""

import uuid

from alembic import op
from sqlalchemy import text

revision = "w034"
down_revision = "w033"
branch_labels = None
depends_on = None

# Same namespace used in kt_db.keys
_KT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-tree")


def _uri_to_source_id(uri: str) -> uuid.UUID:
    """Derive a deterministic UUID from a URI (mirrors kt_db.keys.uri_to_source_id)."""
    return uuid.uuid5(_KT_NAMESPACE, f"source:{uri}")


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: Deduplicate by URI — keep the best row per URI
    # (prefer is_full_text=True, then most recently updated)
    conn.execute(text("""
        DELETE FROM write_raw_sources a
        USING write_raw_sources b
        WHERE a.uri = b.uri
          AND a.id != b.id
          AND (
            (b.is_full_text AND NOT a.is_full_text)
            OR (b.is_full_text = a.is_full_text AND b.updated_at > a.updated_at)
          )
    """))

    # Step 2: Re-ID each source with deterministic UUID from URI
    rows = conn.execute(text("SELECT id, uri FROM write_raw_sources")).fetchall()
    for old_id, uri in rows:
        new_id = _uri_to_source_id(uri)
        if old_id != new_id:
            # Update page_fetch_log references first (nullable FK)
            conn.execute(
                text("UPDATE write_page_fetch_log SET raw_source_id = :new WHERE raw_source_id = :old"),
                {"new": new_id, "old": old_id},
            )
            # Update the source ID
            conn.execute(
                text("UPDATE write_raw_sources SET id = :new WHERE id = :old"),
                {"new": new_id, "old": old_id},
            )

    # Step 3: Reset sync watermarks so sync rebuilds graph-db with new IDs
    conn.execute(text("""
        UPDATE sync_watermarks SET watermark = '1970-01-01'
        WHERE table_name IN ('write_raw_sources', 'write_fact_sources')
    """))


def downgrade() -> None:
    # Cannot reverse deterministic ID assignment — IDs are now derived from URI.
    # Downgrade is a no-op; the old random UUIDs are lost.
    pass
