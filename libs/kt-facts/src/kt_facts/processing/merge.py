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


async def _remap_scalar_refs(
    write_session: AsyncSession,
    loser: str,
    canonical: str,
) -> None:
    """Remap the three scalar fact_id references in write-db.

    Shared between :func:`merge_into_fast` and :func:`merge_into_heavy`
    so that any new table added here is automatically handled by both.

    Does NOT delete the ``write_facts`` row — the caller does that after
    any additional heavy-mode work.
    """
    # write_fact_sources
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

    # write_seed_facts
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

    # write_edge_candidates (fact_id stored as TEXT)
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


async def merge_into_fast(
    write_session: AsyncSession,
    loser_id: uuid.UUID,
    canonical_id: uuid.UUID,
) -> None:
    """Collapse ``loser_id`` into ``canonical_id`` in the write-db only.

    Remaps ``write_fact_sources``, ``write_seed_facts``, and
    ``write_edge_candidates`` via the shared :func:`_remap_scalar_refs`
    helper, then deletes the loser ``write_facts`` row.

    Idempotent when ``loser_id == canonical_id`` (no-op).
    """
    if loser_id == canonical_id:
        return

    loser = str(loser_id)
    canonical = str(canonical_id)

    await _remap_scalar_refs(write_session, loser, canonical)

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
    _pk_column: str = "id",
    *,
    element_type: str = "uuid",
) -> None:
    """Replace ``loser_id`` with ``canonical_id`` in a ``UUID[]`` / ``TEXT[]``
    column and then de-duplicate the resulting array per-row.
    """
    # asyncpg's prepared-statement protocol types bind params as text,
    # which breaks array_replace(uuid[], uuid, uuid). For UUID arrays,
    # embed validated UUID literals directly — UUIDs are a fixed hex+dash
    # format with no injection surface.
    if element_type == "uuid":
        # Validate UUID format before embedding
        loser_uuid = uuid.UUID(loser_id) if isinstance(loser_id, str) else uuid.UUID(str(loser_id))
        canonical_uuid = uuid.UUID(canonical_id) if isinstance(canonical_id, str) else uuid.UUID(str(canonical_id))
        loser_lit = f"'{loser_uuid}'::uuid"
        canonical_lit = f"'{canonical_uuid}'::uuid"
    else:
        loser_lit = ":loser"
        canonical_lit = ":canonical"

    replace_sql = f"""
        UPDATE {table}
           SET {column} = array_replace({column}, {loser_lit}, {canonical_lit})
         WHERE {loser_lit} = ANY({column})
    """
    dedup_sql = f"""
        UPDATE {table}
           SET {column} = ARRAY(
                 SELECT DISTINCT unnest({column})
           )
         WHERE {canonical_lit} = ANY({column})
           AND cardinality({column}) > (
                 SELECT count(DISTINCT e)
                   FROM unnest({column}) AS e
           )
    """

    if element_type == "uuid":
        # No bind params needed — UUIDs are embedded as literals
        await write_session.execute(text(replace_sql))
        await write_session.execute(text(dedup_sql))
    else:
        await write_session.execute(text(replace_sql), {"loser": loser_id, "canonical": canonical_id})
        await write_session.execute(text(dedup_sql), {"canonical": canonical_id})


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

    # ── 1. write-db scalar/fast remap ──
    # Reuse the shared helper that merge_into_fast also calls, but defer
    # the write_facts DELETE until after the heavy-mode array + graph-db work.
    await _remap_scalar_refs(write_session, loser, canonical)

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

    # All four array columns are VARCHAR[] in write-db (not UUID[]).
    await _remap_array_column(write_session, "write_dimensions", "fact_ids", loser, canonical, element_type="text")
    await _remap_array_column(write_session, "write_edges", "fact_ids", loser, canonical, element_type="text")
    await _remap_array_column(write_session, "write_nodes", "fact_ids", loser, canonical, element_type="text")
    await _remap_array_column(
        write_session, "write_seed_merges", "fact_ids_moved", loser, canonical, element_type="text"
    )

    # ── 2. graph-db junction tables ────────────────────────────────────
    # The canonical may not exist in graph-db yet (sync hasn't run for
    # it), so only remap junctions if canonical IS in the ``facts``
    # table. Otherwise just delete the loser's orphaned rows.
    canonical_in_graphdb = (
        await graph_session.execute(
            text("SELECT 1 FROM facts WHERE id = :canonical"),
            {"canonical": canonical},
        )
    ).scalar_one_or_none()

    for junction, fk_col in [
        ("node_facts", "node_id"),
        ("edge_facts", "edge_id"),
        ("dimension_facts", "dimension_id"),
    ]:
        if canonical_in_graphdb:
            await graph_session.execute(
                text(
                    f"""
                    UPDATE {junction} AS j
                       SET fact_id = :canonical
                     WHERE fact_id = :loser
                       AND NOT EXISTS (
                             SELECT 1 FROM {junction} other
                              WHERE other.{fk_col} = j.{fk_col}
                                AND other.fact_id = :canonical
                       )
                    """
                ),
                {"loser": loser, "canonical": canonical},
            )
        await graph_session.execute(
            text(f"DELETE FROM {junction} WHERE fact_id = :loser"),
            {"loser": loser},
        )

    # fact_sources
    if canonical_in_graphdb:
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

    # node_fact_rejections
    if canonical_in_graphdb:
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
