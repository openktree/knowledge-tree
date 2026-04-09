"""Fact merge primitives.

Two flavours:

* :func:`merge_into_fast` — used by the post-job dedup workflow in
  steady state. Only remaps the small set of write-db tables that may
  already reference a just-inserted fact before the autograph step runs:
  ``write_fact_sources``, ``write_seed_facts``, ``write_edge_candidates``.
  Everything else (``write_nodes.fact_ids``, ``write_dimensions.fact_ids``,
  graph-db junctions, Qdrant) cannot yet contain the loser because the
  dedup phase is wedged in between scope extraction and autograph.

* :func:`merge_into_heavy` — used by the one-shot historical repair
  script. Does everything the fast mode does *and* remaps the wider
  set of references that have accumulated in production: array-typed
  columns on ``write_nodes`` / ``write_edges`` / ``write_dimensions`` /
  ``write_seed_merges``, the ``write_node_fact_rejections.fact_id``
  scalar, the graph-db ``node_facts`` / ``edge_facts`` /
  ``dimension_facts`` / ``fact_sources`` junctions, the ``facts`` row
  itself, and the Qdrant point.

Both modes assume the caller owns the enclosing transaction(s) — they
issue statements on the passed sessions and do NOT commit.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


# ── Fast mode ─────────────────────────────────────────────────────────


async def merge_into_fast(
    write_session: AsyncSession,
    loser_id: uuid.UUID,
    canonical_id: uuid.UUID,
) -> None:
    """Collapse ``loser_id`` into ``canonical_id`` in the write-db only.

    Touches (in order):

    1. ``write_fact_sources.fact_id`` — move rows whose ``(canonical,
       raw_source_uri)`` pair does not already exist; drop the rest.
    2. ``write_seed_facts.fact_id`` — move rows whose ``(seed_key,
       canonical)`` pair does not already exist; drop the rest.
    3. ``write_edge_candidates.fact_id`` — move rows whose ``(seed_key_a,
       seed_key_b, canonical)`` triple does not already exist; drop the
       rest. ``fact_id`` is stored as ``String(36)`` on this table.
    4. ``write_facts`` — delete the loser row.

    Idempotent when ``loser_id == canonical_id`` (no-op).
    """
    if loser_id == canonical_id:
        return

    loser = str(loser_id)
    canonical = str(canonical_id)

    # 1. write_fact_sources
    await write_session.execute(
        text(
            """
            UPDATE write_fact_sources AS wfs
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_fact_sources other
                      WHERE other.fact_id = :canonical
                        AND other.raw_source_uri = wfs.raw_source_uri
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_fact_sources WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # 2. write_seed_facts
    await write_session.execute(
        text(
            """
            UPDATE write_seed_facts AS wsf
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_seed_facts other
                      WHERE other.seed_key = wsf.seed_key
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_seed_facts WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # 3. write_edge_candidates (fact_id stored as TEXT here)
    await write_session.execute(
        text(
            """
            UPDATE write_edge_candidates AS wec
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_edge_candidates other
                      WHERE other.seed_key_a = wec.seed_key_a
                        AND other.seed_key_b = wec.seed_key_b
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_edge_candidates WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # 4. write_facts
    await write_session.execute(
        text("DELETE FROM write_facts WHERE id = :loser"),
        {"loser": loser},
    )


# ── Heavy mode ────────────────────────────────────────────────────────


async def _remap_array_column(
    write_session: AsyncSession,
    table: str,
    column: str,
    loser_id: str,
    canonical_id: str,
    pk_column: str = "id",
    *,
    element_type: str = "uuid",
) -> None:
    """Replace ``loser_id`` with ``canonical_id`` in a ``UUID[]`` / ``TEXT[]``
    column and then de-duplicate the resulting array per-row.
    """
    cast = "::uuid" if element_type == "uuid" else ""
    array_param = f":loser{cast}"
    canonical_param = f":canonical{cast}"

    await write_session.execute(
        text(
            f"""
            UPDATE {table}
               SET {column} = array_replace({column}, {array_param}, {canonical_param})
             WHERE {array_param} = ANY({column})
            """
        ),
        {"loser": loser_id, "canonical": canonical_id},
    )
    # De-duplicate the array in case canonical was already present.
    await write_session.execute(
        text(
            f"""
            UPDATE {table}
               SET {column} = ARRAY(
                     SELECT DISTINCT unnest({column})
               )
             WHERE {canonical_param} = ANY({column})
               AND cardinality({column}) > (
                     SELECT count(DISTINCT e)
                       FROM unnest({column}) AS e
               )
            """
        ),
        {"canonical": canonical_id},
    )
    # ``pk_column`` is intentionally unused — the UPDATE targets all rows.
    del pk_column  # noqa: F841 — kept in signature for future row-scoped variants


async def merge_into_heavy(
    write_session: AsyncSession,
    graph_session: AsyncSession,
    qdrant_client: "AsyncQdrantClient | None",
    qdrant_collection: str,
    loser_id: uuid.UUID,
    canonical_id: uuid.UUID,
) -> None:
    """Collapse ``loser_id`` into ``canonical_id`` across write-db,
    graph-db and Qdrant.

    ``qdrant_collection`` is the fully-qualified collection name (e.g.
    ``"facts"`` or ``"myslug__facts"``). The caller resolves the
    prefix based on the target graph.

    This is the one-shot historical repair variant — slower and touches
    far more tables than :func:`merge_into_fast`. Used by
    ``scripts/repair_existing_fact_dups.py``.
    """
    if loser_id == canonical_id:
        return

    loser = str(loser_id)
    canonical = str(canonical_id)

    # ── 1. write-db scalar/fast remap (reuses fast path up to the delete) ──
    # We intentionally do NOT call ``merge_into_fast`` here because we need
    # to also handle the array-typed columns and scalar rejection table
    # before deleting the write_facts row.

    # Fast-mode scalar remaps (same semantics).
    await write_session.execute(
        text(
            """
            UPDATE write_fact_sources AS wfs
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_fact_sources other
                      WHERE other.fact_id = :canonical
                        AND other.raw_source_uri = wfs.raw_source_uri
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_fact_sources WHERE fact_id = :loser"),
        {"loser": loser},
    )
    await write_session.execute(
        text(
            """
            UPDATE write_seed_facts AS wsf
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_seed_facts other
                      WHERE other.seed_key = wsf.seed_key
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_seed_facts WHERE fact_id = :loser"),
        {"loser": loser},
    )
    await write_session.execute(
        text(
            """
            UPDATE write_edge_candidates AS wec
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_edge_candidates other
                      WHERE other.seed_key_a = wec.seed_key_a
                        AND other.seed_key_b = wec.seed_key_b
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_edge_candidates WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # write_node_fact_rejections.fact_id — scalar. Rejections follow
    # canonical; if a rejection already exists we drop the loser's row.
    await write_session.execute(
        text(
            """
            UPDATE write_node_fact_rejections AS r
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM write_node_fact_rejections other
                      WHERE other.node_id = r.node_id
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await write_session.execute(
        text("DELETE FROM write_node_fact_rejections WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # Array-typed columns: write_dimensions / write_edges / write_nodes
    # store ``fact_ids`` as ``UUID[]``; write_seed_merges stores
    # ``fact_ids_moved`` as ``TEXT[]``.
    await _remap_array_column(write_session, "write_dimensions", "fact_ids", loser, canonical, element_type="uuid")
    await _remap_array_column(write_session, "write_edges", "fact_ids", loser, canonical, element_type="uuid")
    await _remap_array_column(write_session, "write_nodes", "fact_ids", loser, canonical, element_type="uuid")
    await _remap_array_column(
        write_session,
        "write_seed_merges",
        "fact_ids_moved",
        loser,
        canonical,
        element_type="text",
    )

    # ── 2. graph-db junction tables ────────────────────────────────────
    await graph_session.execute(
        text(
            """
            UPDATE node_facts AS nf
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM node_facts other
                      WHERE other.node_id = nf.node_id
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await graph_session.execute(
        text("DELETE FROM node_facts WHERE fact_id = :loser"),
        {"loser": loser},
    )

    await graph_session.execute(
        text(
            """
            UPDATE edge_facts AS ef
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM edge_facts other
                      WHERE other.edge_id = ef.edge_id
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await graph_session.execute(
        text("DELETE FROM edge_facts WHERE fact_id = :loser"),
        {"loser": loser},
    )

    await graph_session.execute(
        text(
            """
            UPDATE dimension_facts AS df
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM dimension_facts other
                      WHERE other.dimension_id = df.dimension_id
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await graph_session.execute(
        text("DELETE FROM dimension_facts WHERE fact_id = :loser"),
        {"loser": loser},
    )

    await graph_session.execute(
        text(
            """
            UPDATE fact_sources AS fs
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM fact_sources other
                      WHERE other.fact_id = :canonical
                        AND other.raw_source_id = fs.raw_source_id
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await graph_session.execute(
        text("DELETE FROM fact_sources WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # node_fact_rejections in graph-db (analogous to write-db rejection table).
    await graph_session.execute(
        text(
            """
            UPDATE node_fact_rejections AS r
               SET fact_id = :canonical
             WHERE fact_id = :loser
               AND NOT EXISTS (
                     SELECT 1 FROM node_fact_rejections other
                      WHERE other.node_id = r.node_id
                        AND other.fact_id = :canonical
               )
            """
        ),
        {"loser": loser, "canonical": canonical},
    )
    await graph_session.execute(
        text("DELETE FROM node_fact_rejections WHERE fact_id = :loser"),
        {"loser": loser},
    )

    # The graph-db ``facts`` row itself.
    await graph_session.execute(
        text("DELETE FROM facts WHERE id = :loser"),
        {"loser": loser},
    )

    # ── 3. Qdrant point ────────────────────────────────────────────────
    if qdrant_client is not None:
        try:
            from kt_qdrant.repositories.facts import QdrantFactRepository

            repo = QdrantFactRepository(qdrant_client, collection_name=qdrant_collection)
            await repo.delete_batch([loser_id])
        except Exception:  # pragma: no cover — best effort during historical repair
            logger.warning(
                "merge_into_heavy: failed to delete Qdrant point for loser %s (collection=%s)",
                loser,
                qdrant_collection,
                exc_info=True,
            )

    # ── 4. Finally drop the write_facts row. ──────────────────────────
    await write_session.execute(
        text("DELETE FROM write_facts WHERE id = :loser"),
        {"loser": loser},
    )
