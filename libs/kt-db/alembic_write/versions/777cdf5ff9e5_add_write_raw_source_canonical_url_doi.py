"""add write_raw_source canonical_url doi

Revision ID: 777cdf5ff9e5
Revises: w036
Create Date: 2026-04-08 09:33:38.396449

Adds the multigraph public-cache identity columns to write_raw_sources.

This is the table the PublicGraphBridge actually queries — workers never
touch graph-db. Both ``canonical_url`` and ``doi`` are populated at fetch
time by the ingest pipeline using the helper from PR2
(``kt_providers.fetch.canonical``). Both are non-unique indexes; the same
URL legitimately appears across graph schemas.

Backfill walks existing rows in batches and computes a best-effort
canonical_url + DOI using a minimal helper inlined here so the migration
does not pull a workspace dep on kt-providers. New writes use the full
canonical helper, so a slightly weaker backfill only costs missed cache
hits on legacy rows, never wrong matches.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "777cdf5ff9e5"
down_revision: Union[str, None] = "w036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
_SCHEME_HOST_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/?#]+)")


def _simple_canonicalize(uri: str) -> str | None:
    if not uri:
        return None
    frag_idx = uri.find("#")
    if frag_idx != -1:
        uri = uri[:frag_idx]
    m = _SCHEME_HOST_RE.match(uri)
    if not m:
        return uri or None
    return f"{m.group(1).lower()}{m.group(2).lower()}{uri[m.end() :]}"


def _extract_doi(uri: str) -> str | None:
    if not uri:
        return None
    m = _DOI_RE.search(uri)
    if m:
        # Broader trailing-punctuation strip — backfill sees scraped URIs.
        return m.group(1).rstrip(".),;]")
    return None


def upgrade() -> None:
    op.add_column(
        "write_raw_sources",
        sa.Column("canonical_url", sa.String(2000), nullable=True),
    )
    op.add_column(
        "write_raw_sources",
        sa.Column("doi", sa.String(200), nullable=True),
    )
    op.create_index(
        "ix_write_raw_sources_canonical_url",
        "write_raw_sources",
        ["canonical_url"],
    )
    op.create_index(
        "ix_write_raw_sources_doi",
        "write_raw_sources",
        ["doi"],
    )

    bind = op.get_bind()
    batch_size = 1000
    while True:
        rows = bind.execute(
            sa.text("SELECT id, uri FROM write_raw_sources WHERE canonical_url IS NULL AND uri IS NOT NULL LIMIT :n"),
            {"n": batch_size},
        ).fetchall()
        if not rows:
            break
        for row in rows:
            canonical = _simple_canonicalize(row.uri)
            doi = _extract_doi(row.uri)
            bind.execute(
                sa.text("UPDATE write_raw_sources SET canonical_url = :canonical, doi = :doi WHERE id = :id"),
                {"canonical": canonical, "doi": doi, "id": row.id},
            )
        if len(rows) < batch_size:
            break


def downgrade() -> None:
    op.drop_index("ix_write_raw_sources_doi", table_name="write_raw_sources")
    op.drop_index(
        "ix_write_raw_sources_canonical_url",
        table_name="write_raw_sources",
    )
    op.drop_column("write_raw_sources", "doi")
    op.drop_column("write_raw_sources", "canonical_url")
