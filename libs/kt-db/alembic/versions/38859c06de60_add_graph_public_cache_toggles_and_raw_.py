"""add graph public cache toggles and raw_source canonical_url doi

Revision ID: 38859c06de60
Revises: b6d5ae365bff
Create Date: 2026-04-08 09:33:23.242091

Adds two paired pieces of multigraph public-cache state:

1. ``graphs.contribute_to_public`` and ``graphs.use_public_cache`` — per-graph
   toggles (default ON for non-default graphs). The default graph itself
   ignores them in code since it has no upstream. Lives only in the public
   schema (control plane).

2. ``raw_sources.canonical_url`` and ``raw_sources.doi`` — per-source identity
   keys for cross-graph cache lookup. These live in *every* graph schema
   because raw_sources is per-schema. Both columns are non-unique indexes;
   the bridge queries write-db (this is the read-side mirror).

Backfill walks existing rows and computes a best-effort canonical_url +
DOI. Imperfect backfill only costs missed cache hits, never wrong matches,
so we keep the helper minimal here and rely on the full
``kt_providers.fetch.canonical`` helper for new writes.
"""

import os
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "38859c06de60"
down_revision: Union[str, Sequence[str], None] = "b6d5ae365bff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
_SCHEME_HOST_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/?#]+)")


def _simple_canonicalize(uri: str) -> str | None:
    """Minimal canonicalisation for backfill.

    Lowercases scheme + host and drops fragment. Tracker stripping and
    duplicate-slash collapsing are intentionally omitted — the bridge
    tolerates a missed cache hit on legacy rows, and new writes use the
    full helper from ``kt_providers.fetch.canonical``. Returns ``None``
    for empty/garbage URIs so we don't write empty strings.
    """
    if not uri:
        return None
    # Drop fragment.
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
        # Trim a broader set of trailing punctuation often picked up
        # from prose / scraped text.
        return m.group(1).rstrip(".),;]")
    return None


def upgrade() -> None:
    schema = os.environ.get("ALEMBIC_SCHEMA")

    # ----- Per-schema: raw_sources columns + indexes + backfill -----
    op.add_column(
        "raw_sources",
        sa.Column("canonical_url", sa.String(2000), nullable=True),
    )
    op.add_column(
        "raw_sources",
        sa.Column("doi", sa.String(200), nullable=True),
    )
    op.create_index(
        "ix_raw_sources_canonical_url",
        "raw_sources",
        ["canonical_url"],
    )
    op.create_index(
        "ix_raw_sources_doi",
        "raw_sources",
        ["doi"],
    )

    # Backfill in batches.
    bind = op.get_bind()
    batch_size = 1000
    while True:
        rows = bind.execute(
            sa.text("SELECT id, uri FROM raw_sources WHERE canonical_url IS NULL AND uri IS NOT NULL LIMIT :n"),
            {"n": batch_size},
        ).fetchall()
        if not rows:
            break
        for row in rows:
            canonical = _simple_canonicalize(row.uri)
            doi = _extract_doi(row.uri)
            bind.execute(
                sa.text("UPDATE raw_sources SET canonical_url = :canonical, doi = :doi WHERE id = :id"),
                {"canonical": canonical, "doi": doi, "id": row.id},
            )
        if len(rows) < batch_size:
            break

    # ----- Public-only: graphs toggle columns -----
    if schema and schema != "public":
        return
    op.add_column(
        "graphs",
        sa.Column(
            "contribute_to_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "use_public_cache",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    schema = os.environ.get("ALEMBIC_SCHEMA")
    if not schema or schema == "public":
        op.drop_column("graphs", "use_public_cache")
        op.drop_column("graphs", "contribute_to_public")
    op.drop_index("ix_raw_sources_doi", table_name="raw_sources")
    op.drop_index("ix_raw_sources_canonical_url", table_name="raw_sources")
    op.drop_column("raw_sources", "doi")
    op.drop_column("raw_sources", "canonical_url")
