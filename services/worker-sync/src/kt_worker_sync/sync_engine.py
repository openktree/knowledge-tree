"""Sync engine: reads incremental changes from write-db and applies to graph-db.

Single-threaded to avoid any concurrency issues on graph-db writes.
Polls by updated_at > watermark for each table.

Uses deterministic UUIDs derived from write-db keys via ``key_to_uuid()``
so both databases share the same identity for every entity.

Resilience features:
- Inner savepoints around junction-row inserts (NodeFact, EdgeFact,
  DimensionFact, FactSource) so a single FK violation doesn't poison the
  enclosing transaction.
- Safe watermark advancement: on any failure the watermark freezes at the
  timestamp of the first failed record, guaranteeing a retry next cycle.
- Dead-letter queue (``sync_failures`` table) with exponential backoff
  for records that persistently fail.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update  # noqa: I001
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kt_config.settings import get_settings
from kt_db.keys import key_to_uuid
from kt_db.models import (
    ConvergenceReport,
    Dimension,
    DimensionFact,
    Edge,
    EdgeFact,
    Fact,
    FactSource,
    LlmUsage,
    Node,
    NodeCounter,
    NodeFact,
    NodeVersion,
    ProhibitedChunk,
    RawSource,
)
from kt_db.write_models import (
    SyncFailure,
    SyncWatermark,
    WriteConvergenceReport,
    WriteDimension,
    WriteEdge,
    WriteFact,
    WriteFactSource,
    WriteLlmUsage,
    WriteNode,
    WriteNodeCounter,
    WriteNodeFactRejection,
    WriteNodeVersion,
    WriteProhibitedChunk,
    WriteRawSource,
)

logger = logging.getLogger(__name__)

# Epoch used as initial watermark
_EPOCH = datetime(1970, 1, 1)

# Namespace for deterministic UUID5 from non-UUID strings
_USAGE_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _safe_uuid(value: str | None) -> uuid.UUID:
    """Parse a string as UUID, falling back to UUID5 for non-UUID strings."""
    if not value:
        return uuid.UUID(int=0)
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(_USAGE_NS, value)


class SyncEngine:
    """Incremental sync from write-db to graph-db."""

    def __init__(
        self,
        write_session_factory: async_sessionmaker[AsyncSession],
        graph_session_factory: async_sessionmaker[AsyncSession],
        embedding_service: object | None = None,
        batch_size: int = 100,
        qdrant_client: object | None = None,
        graph_slug: str = "default",
    ) -> None:
        self._write_sf = write_session_factory
        self._graph_sf = graph_session_factory
        self._embedding_service = embedding_service
        self._batch_size = batch_size
        self._graph_slug = graph_slug
        self._qdrant_node_repo = None
        if qdrant_client is not None:
            from kt_qdrant.repositories.nodes import QdrantNodeRepository

            self._qdrant_node_repo = QdrantNodeRepository(qdrant_client)

    async def sync_cycle(self) -> dict[str, int]:
        """Run one full sync cycle across all tables. Returns counts per table.

        Facts are synced FIRST so that junction rows (NodeFact, EdgeFact,
        DimensionFact) created during node/edge/dimension sync can reference
        existing Fact rows in graph-db.

        After the main sync pass, retries any previously-failed records from
        the dead-letter queue whose backoff period has elapsed.
        """
        t0 = time.monotonic()
        counts: dict[str, int] = {}

        tables = [
            ("write_raw_sources", self._sync_raw_sources),
            ("write_prohibited_chunks", self._sync_prohibited_chunks),
            ("write_facts", self._sync_facts),
            ("write_nodes", self._sync_nodes),
            ("write_edges", self._sync_edges),
            ("write_dimensions", self._sync_dimensions),
            ("write_convergence_reports", self._sync_convergence),
            ("write_node_counters", self._sync_counters),
            ("write_node_versions", self._sync_node_versions),
            ("write_llm_usage", self._sync_llm_usage),
        ]

        for table_name, sync_fn in tables:
            table_t0 = time.monotonic()
            try:
                counts[table_name] = await sync_fn()
            except Exception:
                logger.error(
                    "Sync FAILED for table %s after %.2fs",
                    table_name,
                    time.monotonic() - table_t0,
                    exc_info=True,
                )
                counts[table_name] = 0
                continue
            table_elapsed = time.monotonic() - table_t0
            if counts[table_name] > 0:
                logger.info(
                    "Synced %d %s records in %.2fs",
                    counts[table_name],
                    table_name,
                    table_elapsed,
                )

        # Retry previously-failed records
        retried = await self._retry_failed_syncs()
        if retried > 0:
            counts["retried_failures"] = retried

        total = sum(counts.values())
        elapsed = time.monotonic() - t0
        if total > 0:
            logger.info("Sync cycle complete: %d total records in %.2fs — %s", total, elapsed, counts)
        return counts

    async def _get_watermark(self, write_session: AsyncSession, table_name: str) -> datetime:
        result = await write_session.execute(
            select(SyncWatermark.last_synced_at).where(
                SyncWatermark.table_name == table_name,
                SyncWatermark.graph_slug == self._graph_slug,
            )
        )
        row = result.scalar_one_or_none()
        return row if row is not None else _EPOCH

    async def _set_watermark(self, write_session: AsyncSession, table_name: str, ts: datetime) -> None:
        stmt = (
            pg_insert(SyncWatermark)
            .values(table_name=table_name, graph_slug=self._graph_slug, last_synced_at=ts)
            .on_conflict_do_update(
                index_elements=[SyncWatermark.table_name, SyncWatermark.graph_slug],
                set_={"last_synced_at": ts},
            )
        )
        await write_session.execute(stmt)

    # ── Denormalized stat refresh ────────────────────────────────────────

    async def _refresh_node_stats(
        self,
        gs: AsyncSession,
        node_ids: set[uuid.UUID],
    ) -> None:
        """Recompute denormalized counters for the given nodes.

        Runs efficient aggregate subqueries and batch-updates the nodes table.
        Called after syncing entities that affect node stats (facts, edges,
        dimensions, convergence, child relationships).
        """
        if not node_ids:
            return

        id_list = list(node_ids)

        # fact_count — use LEFT JOIN so nodes with zero facts get 0
        await gs.execute(
            sa_text(
                """
                UPDATE nodes SET fact_count = COALESCE(sub.cnt, 0)
                FROM (
                    SELECT u.id AS node_id, COUNT(nf.fact_id) AS cnt
                    FROM UNNEST(CAST(:ids AS uuid[])) AS u(id)
                    LEFT JOIN node_facts nf ON nf.node_id = u.id
                    GROUP BY u.id
                ) sub
                WHERE nodes.id = sub.node_id
                """
            ),
            {"ids": id_list},
        )

        # edge_count (source + target) — LEFT JOIN so deleted edges → 0
        await gs.execute(
            sa_text(
                """
                UPDATE nodes SET edge_count = COALESCE(sub.cnt, 0)
                FROM (
                    SELECT u.id AS node_id, COALESCE(SUM(e.cnt), 0)::int AS cnt
                    FROM UNNEST(CAST(:ids AS uuid[])) AS u(id)
                    LEFT JOIN (
                        SELECT source_node_id AS node_id, COUNT(*) AS cnt
                        FROM edges WHERE source_node_id = ANY(:ids)
                        GROUP BY source_node_id
                        UNION ALL
                        SELECT target_node_id AS node_id, COUNT(*) AS cnt
                        FROM edges WHERE target_node_id = ANY(:ids)
                        GROUP BY target_node_id
                    ) e ON e.node_id = u.id
                    GROUP BY u.id
                ) sub
                WHERE nodes.id = sub.node_id
                """
            ),
            {"ids": id_list},
        )

        # child_count — LEFT JOIN so nodes with no children → 0
        await gs.execute(
            sa_text(
                """
                UPDATE nodes SET child_count = COALESCE(sub.cnt, 0)
                FROM (
                    SELECT u.id AS node_id, COUNT(c.id) AS cnt
                    FROM UNNEST(CAST(:ids AS uuid[])) AS u(id)
                    LEFT JOIN nodes c ON c.parent_id = u.id
                    GROUP BY u.id
                ) sub
                WHERE nodes.id = sub.node_id
                """
            ),
            {"ids": id_list},
        )

        # dimension_count — LEFT JOIN so nodes with no dimensions → 0
        await gs.execute(
            sa_text(
                """
                UPDATE nodes SET dimension_count = COALESCE(sub.cnt, 0)
                FROM (
                    SELECT u.id AS node_id, COUNT(d.id) AS cnt
                    FROM UNNEST(CAST(:ids AS uuid[])) AS u(id)
                    LEFT JOIN dimensions d ON d.node_id = u.id
                    GROUP BY u.id
                ) sub
                WHERE nodes.id = sub.node_id
                """
            ),
            {"ids": id_list},
        )

        # convergence_score
        conv_sub = (
            select(ConvergenceReport.node_id, ConvergenceReport.convergence_score.label("score"))
            .where(ConvergenceReport.node_id.in_(id_list))
            .subquery()
        )
        await gs.execute(update(Node).where(Node.id == conv_sub.c.node_id).values(convergence_score=conv_sub.c.score))

        # Invalidate cached API responses for affected nodes
        from kt_config.cache import cache_invalidate

        for nid in node_ids:
            await cache_invalidate(f"kt:node:{nid}*")
        await cache_invalidate("kt:nodes:list:*")
        await cache_invalidate("kt:graph:subgraph:*")
        await cache_invalidate("kt:graph:stats")

    # ── Raw Sources ──────────────────────────────────────────────────────

    async def _sync_raw_sources(self) -> int:
        """Sync WriteRawSource from write-db to graph-db.

        Must run BEFORE facts because FactSource references RawSource via FK.
        Uses content_hash for dedup (same as graph-db SourceRepository).
        """
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_raw_sources")
            rows = (
                (
                    await ws.execute(
                        select(WriteRawSource)
                        .where(WriteRawSource.updated_at > watermark)
                        .order_by(WriteRawSource.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d raw_sources (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wrs in rows:
                try:
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(RawSource.__table__)
                            .values(
                                id=wrs.id,
                                uri=wrs.uri,
                                title=wrs.title,
                                raw_content=wrs.raw_content,
                                content_hash=wrs.content_hash,
                                is_full_text=wrs.is_full_text,
                                is_super_source=wrs.is_super_source,
                                content_type=wrs.content_type,
                                provider_id=wrs.provider_id,
                                provider_metadata=wrs.provider_metadata,
                                fact_count=wrs.fact_count,
                                prohibited_chunk_count=wrs.prohibited_chunk_count,
                                fetch_attempted=wrs.fetch_attempted,
                                fetch_error=wrs.fetch_error,
                            )
                            .on_conflict_do_update(
                                index_elements=["id"],
                                set_={
                                    "uri": wrs.uri,
                                    "title": wrs.title,
                                    "raw_content": wrs.raw_content,
                                    "content_hash": wrs.content_hash,
                                    "is_full_text": wrs.is_full_text,
                                    "is_super_source": wrs.is_super_source,
                                    "content_type": wrs.content_type,
                                    "provider_metadata": wrs.provider_metadata,
                                    # fact_count intentionally excluded — managed
                                    # by _sync_one_fact_source increments only.
                                    # write-db never updates fact_count, so
                                    # re-syncing would reset it to 0.
                                    "prohibited_chunk_count": wrs.prohibited_chunk_count,
                                    "fetch_attempted": wrs.fetch_attempted,
                                    "fetch_error": wrs.fetch_error,
                                },
                            )
                        )
                        await gs.execute(stmt)

                    count += 1
                    if wrs.updated_at > max_ts:
                        max_ts = wrs.updated_at
                except Exception as exc:
                    logger.warning(
                        "Failed to sync raw_source %s",
                        wrs.id,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wrs.updated_at
                    await self._record_failure(ws, "write_raw_sources", str(wrs.id), exc)

            safe_ts = first_failure_ts if first_failure_ts is not None else max_ts
            if safe_ts > watermark:
                await self._set_watermark(ws, "write_raw_sources", safe_ts)
            await ws.commit()
            await gs.commit()
            return count

    # ── Prohibited Chunks ────────────────────────────────────────────

    async def _sync_prohibited_chunks(self) -> int:
        """Sync WriteProhibitedChunk from write-db to graph-db.

        Resolves source_content_hash → raw_source_id via graph-db RawSource.
        Must run AFTER raw_sources sync.
        """
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_prohibited_chunks")
            rows = (
                (
                    await ws.execute(
                        select(WriteProhibitedChunk)
                        .where(WriteProhibitedChunk.updated_at > watermark)
                        .order_by(WriteProhibitedChunk.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d prohibited_chunks (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wpc in rows:
                try:
                    # Resolve content_hash → raw_source_id
                    result = await gs.execute(
                        select(RawSource.id).where(RawSource.content_hash == wpc.source_content_hash)
                    )
                    raw_source_id = result.scalar_one_or_none()
                    if raw_source_id is None:
                        logger.warning(
                            "No RawSource for content_hash %s, skipping prohibited chunk %s",
                            wpc.source_content_hash,
                            wpc.id,
                        )
                        if wpc.updated_at > max_ts:
                            max_ts = wpc.updated_at
                        continue

                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(ProhibitedChunk.__table__)
                            .values(
                                id=wpc.id,
                                raw_source_id=raw_source_id,
                                chunk_text=wpc.chunk_text,
                                model_id=wpc.model_id,
                                fallback_model_id=wpc.fallback_model_id,
                                error_message=wpc.error_message,
                                created_at=wpc.created_at,
                            )
                            .on_conflict_do_nothing(index_elements=["id"])
                        )
                        await gs.execute(stmt)

                    count += 1
                    if wpc.updated_at > max_ts:
                        max_ts = wpc.updated_at
                except Exception as exc:
                    logger.warning(
                        "Failed to sync prohibited_chunk %s",
                        wpc.id,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wpc.updated_at
                    await self._record_failure(ws, "write_prohibited_chunks", str(wpc.id), exc)

            safe_ts = first_failure_ts if first_failure_ts is not None else max_ts
            if safe_ts > watermark:
                await self._set_watermark(ws, "write_prohibited_chunks", safe_ts)
            await ws.commit()
            await gs.commit()
            return count

    # ── Facts ──────────────────────────────────────────────────────────

    async def _sync_facts(self) -> int:
        """Sync WriteFact + WriteFactSource from write-db to graph-db.

        Must run BEFORE nodes/edges/dimensions because their junction rows
        (NodeFact, EdgeFact, DimensionFact) reference Fact.id via FK.

        For each WriteFact:
          1. Upsert into graph-db ``facts`` table (same UUID)
          2. For each WriteFactSource:
             - Find or create RawSource by content_hash
             - Create FactSource junction row
        """
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_facts")
            rows = (
                (
                    await ws.execute(
                        select(WriteFact)
                        .where(WriteFact.updated_at > watermark)
                        .order_by(WriteFact.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d facts (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wf in rows:
                try:
                    async with gs.begin_nested():
                        # Atomic upsert fact into graph-db
                        stmt = (
                            pg_insert(Fact.__table__)
                            .values(
                                id=wf.id,
                                content=wf.content,
                                fact_type=wf.fact_type,
                                metadata=wf.metadata_,
                            )
                            .on_conflict_do_update(
                                index_elements=[Fact.__table__.c.id],
                                set_={
                                    "content": wf.content,
                                    "fact_type": wf.fact_type,
                                    "metadata": wf.metadata_,
                                },
                            )
                        )
                        await gs.execute(stmt)

                        # Sync fact sources
                        fact_sources = (
                            (await ws.execute(select(WriteFactSource).where(WriteFactSource.fact_id == wf.id)))
                            .scalars()
                            .all()
                        )

                        for wfs in fact_sources:
                            await self._sync_one_fact_source(ws, gs, wf.id, wfs)
                except Exception as exc:
                    logger.error("Failed to sync fact %s, skipping", wf.id, exc_info=True)
                    if first_failure_ts is None:
                        first_failure_ts = wf.updated_at
                    await self._record_failure(ws, "write_facts", str(wf.id), exc)
                    continue

                if wf.updated_at > max_ts:
                    max_ts = wf.updated_at
                count += 1

            # Safe watermark: freeze at first failure so failed records are retried
            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Facts: %d/%d synced, %d failed — watermark frozen at %s (first failure)",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_facts", safe_ts)
            await ws.commit()
            return count

    async def _sync_one_fact_source(
        self,
        ws: AsyncSession,
        gs: AsyncSession,
        fact_id: uuid.UUID,
        wfs: WriteFactSource,
    ) -> None:
        """Sync a single WriteFactSource to graph-db.

        Finds or creates the RawSource by content_hash, then creates the
        FactSource junction row.  When creating a new RawSource, looks up the
        real source in write-db first to avoid creating content-less phantoms.
        """
        # Find existing RawSource by content_hash
        raw_source_id: uuid.UUID | None = None
        if wfs.raw_source_content_hash:
            result = await gs.execute(
                select(RawSource.id).where(RawSource.content_hash == wfs.raw_source_content_hash).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                raw_source_id = row

        # If not found, look up the real source in write-db before creating
        if raw_source_id is None:
            from kt_db.repositories.write_sources import WriteSourceRepository

            write_repo = WriteSourceRepository(ws)
            real_source = await write_repo.get_by_content_hash(wfs.raw_source_content_hash or "")

            if real_source is not None:
                # Upsert with full content from write-db (no phantom)
                stmt = (
                    pg_insert(RawSource.__table__)
                    .values(
                        id=real_source.id,
                        uri=real_source.uri,
                        title=real_source.title,
                        raw_content=real_source.raw_content,
                        content_hash=real_source.content_hash,
                        is_full_text=real_source.is_full_text,
                        is_super_source=real_source.is_super_source,
                        content_type=real_source.content_type,
                        provider_id=real_source.provider_id,
                        provider_metadata=real_source.provider_metadata,
                        fetch_attempted=real_source.fetch_attempted,
                        fetch_error=real_source.fetch_error,
                    )
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "uri": real_source.uri,
                            "title": real_source.title,
                            "raw_content": real_source.raw_content,
                            "content_hash": real_source.content_hash,
                            "is_full_text": real_source.is_full_text,
                            "is_super_source": real_source.is_super_source,
                            "content_type": real_source.content_type,
                            "provider_metadata": real_source.provider_metadata,
                            "fetch_attempted": real_source.fetch_attempted,
                            "fetch_error": real_source.fetch_error,
                        },
                    )
                    .returning(RawSource.__table__.c.id)
                )
                result = await gs.execute(stmt)
                raw_source_id = result.scalar_one_or_none()
                if raw_source_id is None:
                    # content_hash conflict with different ID — re-query
                    result2 = await gs.execute(
                        select(RawSource.id).where(RawSource.content_hash == real_source.content_hash).limit(1)
                    )
                    raw_source_id = result2.scalar_one_or_none()
            else:
                # Fallback: create minimal record without content (source not in write-db).
                # This can still produce phantom sources — monitor for occurrences.
                logger.warning(
                    "Creating minimal RawSource for hash %s (not found in write-db) — potential phantom",
                    wfs.raw_source_content_hash,
                )
                from kt_db.keys import uri_to_source_id as _uri_to_source_id

                if not wfs.raw_source_uri:
                    logger.error(
                        "WriteFactSource %s has no URI — cannot derive deterministic source ID, skipping",
                        wfs.id,
                    )
                    return
                raw_source_id = _uri_to_source_id(wfs.raw_source_uri)
                stmt = (
                    pg_insert(RawSource.__table__)
                    .values(
                        id=raw_source_id,
                        uri=wfs.raw_source_uri,
                        title=wfs.raw_source_title,
                        provider_id=wfs.raw_source_provider_id,
                        content_hash=wfs.raw_source_content_hash or "",
                    )
                    .on_conflict_do_nothing()
                    .returning(RawSource.__table__.c.id)
                )
                result = await gs.execute(stmt)
                returned = result.scalar_one_or_none()
                if returned is None:
                    result2 = await gs.execute(
                        select(RawSource.id).where(RawSource.content_hash == wfs.raw_source_content_hash).limit(1)
                    )
                    raw_source_id = result2.scalar_one_or_none()
                else:
                    raw_source_id = returned

            if raw_source_id is None:
                logger.debug(
                    "Failed to find or create RawSource for hash %s",
                    wfs.raw_source_content_hash,
                )
                return

        # Create FactSource junction (inner savepoint protects enclosing txn)
        try:
            async with gs.begin_nested():
                stmt = (
                    pg_insert(FactSource.__table__)
                    .values(
                        id=uuid.uuid4(),
                        fact_id=fact_id,
                        raw_source_id=raw_source_id,
                        context_snippet=wfs.context_snippet,
                        attribution=wfs.attribution,
                        author_person=wfs.author_person,
                        author_org=wfs.author_org,
                    )
                    .on_conflict_do_nothing()
                    .returning(FactSource.__table__.c.id)
                )
                result = await gs.execute(stmt)
                inserted = result.scalar_one_or_none()
                if inserted is not None:
                    # Increment cached fact_count on RawSource
                    await gs.execute(
                        update(RawSource)
                        .where(RawSource.id == raw_source_id)
                        .values(fact_count=RawSource.fact_count + 1)
                    )
        except Exception:
            logger.debug(
                "Failed to create FactSource for fact %s -> source %s",
                fact_id,
                raw_source_id,
                exc_info=True,
            )

    # ── Nodes ──────────────────────────────────────────────────────────

    async def _sync_nodes(self) -> int:
        # Phase 1: Read from write-db, then release the connection.
        # This avoids holding a write-db session idle through pgbouncer
        # during the long graph-db upsert phase.
        async with self._write_sf() as ws:
            watermark = await self._get_watermark(ws, "write_nodes")
            rows = (
                (
                    await ws.execute(
                        select(WriteNode)
                        .where(WriteNode.updated_at > watermark)
                        .order_by(WriteNode.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            # Pre-load rejected fact IDs for all nodes in this batch so the
            # upsert can skip creating NodeFact rows for rejected facts.
            all_node_uuids = [wn.node_uuid for wn in rows]
            rejection_rows = (
                await ws.execute(
                    select(
                        WriteNodeFactRejection.node_id,
                        WriteNodeFactRejection.fact_id,
                    ).where(WriteNodeFactRejection.node_id.in_(all_node_uuids))
                )
            ).all()
            rejected_by_node: dict[uuid.UUID, set[str]] = {}
            for nid, fid in rejection_rows:
                rejected_by_node.setdefault(nid, set()).add(str(fid))
            # Detach ORM objects before closing the session
            ws.expunge_all()

        logger.info(
            "Syncing %d nodes (watermark=%s)",
            len(rows),
            watermark.isoformat(),
        )

        max_ts = watermark
        count = 0
        first_failure_ts: datetime | None = None
        pending_failures: list[tuple[str, Exception]] = []

        # Phase 2: Upsert into graph-db (no write-db session held).
        async with self._graph_sf() as gs:
            # Pass 1: upsert all nodes WITHOUT self-referencing FKs (parent_id,
            # source_concept_id) to avoid FK violations when referenced nodes
            # haven't been synced yet.
            deferred_refs: list[tuple[str, uuid.UUID, uuid.UUID]] = []  # (column, node_id, ref_id)
            for wn in rows:
                node_id = wn.node_uuid
                rejected_fids = rejected_by_node.get(node_id, set())
                try:
                    async with gs.begin_nested():
                        await self._upsert_graph_node(
                            gs,
                            wn,
                            skip_parent=True,
                            rejected_fact_ids=rejected_fids,
                        )
                except Exception as exc:
                    logger.error("Failed to sync node %s, skipping", wn.key, exc_info=True)
                    if first_failure_ts is None:
                        first_failure_ts = wn.updated_at
                    pending_failures.append((wn.key, exc))
                    continue

                if wn.parent_key:
                    deferred_refs.append(("parent_id", node_id, key_to_uuid(wn.parent_key)))
                if wn.source_concept_key:
                    deferred_refs.append(("source_concept_id", node_id, key_to_uuid(wn.source_concept_key)))
                if wn.updated_at > max_ts:
                    max_ts = wn.updated_at
                count += 1

            # Pass 2: set self-referencing FKs where the target exists in graph-db
            for col, node_id, ref_id in deferred_refs:
                try:
                    async with gs.begin_nested():
                        ref_exists = (await gs.execute(select(Node.id).where(Node.id == ref_id))).scalar_one_or_none()
                        if ref_exists is not None:
                            await gs.execute(update(Node).where(Node.id == node_id).values(**{col: ref_id}))
                        else:
                            logger.debug(
                                "Skipping %s for node %s: target %s not yet in graph-db",
                                col,
                                node_id,
                                ref_id,
                            )
                except Exception:
                    logger.error(
                        "Failed to set %s for node %s, skipping",
                        col,
                        node_id,
                        exc_info=True,
                    )

            if deferred_refs:
                logger.debug(
                    "Nodes pass 2: resolving %d deferred FK refs",
                    len(deferred_refs),
                )

            # Refresh denormalized stats for synced nodes and their parents
            affected_ids = set(all_node_uuids)
            for _, _, ref_id in deferred_refs:
                affected_ids.add(ref_id)
            await self._refresh_node_stats(gs, affected_ids)

            await gs.commit()

        # Phase 3: Update watermark and record failures in write-db.
        safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
        if first_failure_ts is not None:
            failed = len(rows) - count
            logger.warning(
                "Nodes: %d/%d synced, %d failed — watermark frozen at %s (first failure)",
                count,
                len(rows),
                failed,
                safe_ts.isoformat(),
            )

        async with self._write_sf() as ws:
            for key, exc in pending_failures:
                await self._record_failure(ws, "write_nodes", key, exc)
            await self._set_watermark(ws, "write_nodes", safe_ts)
            await ws.commit()

        # Repair orphaned parent/source_concept refs from previous cycles.
        await self._repair_orphaned_node_refs()

        return count

    async def _repair_orphaned_node_refs(self) -> None:
        """Resolve parent_id / source_concept_id that were skipped in earlier cycles.

        Scans write-db for nodes that have parent_key set, then checks graph-db
        for the corresponding node having parent_id IS NULL.  If the parent now
        exists in graph-db, sets the FK.  Capped to avoid unbounded work.

        Uses its own short-lived sessions to avoid holding a write-db connection
        idle during graph-db operations (which can exceed idle_in_transaction
        timeout on large batches).
        """
        # Read candidates from write-db in a short-lived read-only session
        async with self._write_sf() as ws:
            candidates = (
                await ws.execute(
                    select(WriteNode.key, WriteNode.parent_key, WriteNode.source_concept_key)
                    .where(WriteNode.parent_key.isnot(None))
                    .limit(500)
                )
            ).all()
            await ws.rollback()  # read-only — release connection cleanly

        if not candidates:
            return

        # Repair in graph-db using its own session
        async with self._graph_sf() as gs:
            repaired = 0
            for row in candidates:
                node_id = key_to_uuid(row.key)

                for col, ref_key in [
                    ("parent_id", row.parent_key),
                    ("source_concept_id", row.source_concept_key),
                ]:
                    if not ref_key:
                        continue
                    ref_id = key_to_uuid(ref_key)

                    try:
                        async with gs.begin_nested():
                            # Check if graph-db node exists with this FK still NULL
                            needs_repair = (
                                await gs.execute(
                                    select(Node.id).where(Node.id == node_id).where(getattr(Node, col).is_(None))
                                )
                            ).scalar_one_or_none()

                            if needs_repair is None:
                                # Either node doesn't exist yet, or FK is already set
                                continue

                            # Check if the referenced parent/source exists
                            ref_exists = (
                                await gs.execute(select(Node.id).where(Node.id == ref_id))
                            ).scalar_one_or_none()
                            if ref_exists is None:
                                continue

                            await gs.execute(update(Node).where(Node.id == node_id).values(**{col: ref_id}))
                            repaired += 1
                    except Exception:
                        logger.error(
                            "Repair: failed to set %s for node %s",
                            col,
                            node_id,
                            exc_info=True,
                        )

            if repaired > 0:
                logger.info("Repaired %d orphaned node refs (parent_id / source_concept_id)", repaired)
            await gs.commit()

    async def _upsert_graph_node(
        self,
        gs: AsyncSession,
        wn: WriteNode,
        *,
        skip_parent: bool = False,
        rejected_fact_ids: set[str] | None = None,
    ) -> None:
        """Upsert a node from write-db into graph-db using deterministic UUID.

        Uses atomic pg_insert ON CONFLICT DO UPDATE to avoid transaction-
        poisoning failures from select-then-insert race conditions.

        When *skip_parent* is True, parent_id is NOT set on this pass —
        the caller is responsible for setting it in a second pass after all
        nodes in the batch have been inserted (avoids FK violations when the
        parent hasn't been synced yet).

        *rejected_fact_ids* contains stringified UUIDs of facts that were
        rejected during dimension generation. These are skipped when creating
        NodeFact junction rows.
        """
        node_id = wn.node_uuid

        values: dict = {
            "id": node_id,
            "concept": wn.concept,
            "node_type": wn.node_type,
            "entity_subtype": wn.entity_subtype,
            "definition": wn.definition,
            "definition_source": wn.definition_source,
            "attractor": wn.attractor,
            "filter_id": wn.filter_id,
            "max_content_tokens": wn.max_content_tokens,
            "stale_after": wn.stale_after,
            "enrichment_status": wn.enrichment_status,
            "metadata": wn.metadata_,
        }
        if not skip_parent:
            values["parent_id"] = key_to_uuid(wn.parent_key) if wn.parent_key else None
            values["source_concept_id"] = key_to_uuid(wn.source_concept_key) if wn.source_concept_key else None

        update_values = {k: v for k, v in values.items() if k != "id"}

        stmt = (
            pg_insert(Node.__table__)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Node.__table__.c.id],
                set_=update_values,
            )
        )
        await gs.execute(stmt)

        # Create NodeFact junction rows from stored fact_ids, skipping any
        # facts that were rejected during dimension generation.
        # Each insert is wrapped in an inner savepoint so a single FK violation
        # (e.g. fact not yet synced) doesn't poison the enclosing transaction.
        _rejected = rejected_fact_ids or set()
        if wn.fact_ids:
            for fid_str in wn.fact_ids:
                if fid_str in _rejected:
                    continue
                try:
                    fact_id = uuid.UUID(fid_str)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(NodeFact.__table__)
                            .values(node_id=node_id, fact_id=fact_id, relevance_score=1.0)
                            .on_conflict_do_nothing()
                        )
                        await gs.execute(stmt)
                except (ValueError, Exception):
                    logger.debug("Failed to link fact %s to node %s", fid_str, wn.key)

    # ── Edges ──────────────────────────────────────────────────────────

    async def _sync_edges(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_edges")
            rows = (
                (
                    await ws.execute(
                        select(WriteEdge)
                        .where(WriteEdge.updated_at > watermark)
                        .order_by(WriteEdge.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d edges (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for we in rows:
                try:
                    async with gs.begin_nested():
                        await self._upsert_graph_edge(gs, we)
                except Exception as exc:
                    logger.error("Failed to sync edge %s, skipping", we.key, exc_info=True)
                    if first_failure_ts is None:
                        first_failure_ts = we.updated_at
                    await self._record_failure(ws, "write_edges", we.key, exc)
                    continue
                if we.updated_at > max_ts:
                    max_ts = we.updated_at
                count += 1

            # Refresh edge_count on all affected nodes
            edge_node_ids: set[uuid.UUID] = set()
            for we in rows:
                edge_node_ids.add(key_to_uuid(we.source_node_key))
                edge_node_ids.add(key_to_uuid(we.target_node_key))
            await self._refresh_node_stats(gs, edge_node_ids)

            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Edges: %d/%d synced, %d failed — watermark frozen at %s (first failure)",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_edges", safe_ts)
            await ws.commit()
            return count

    async def _upsert_graph_edge(self, gs: AsyncSession, we: WriteEdge) -> None:
        """Upsert an edge from write-db into graph-db using deterministic UUIDs.

        Uses atomic pg_insert ON CONFLICT DO UPDATE to avoid transaction-
        poisoning failures from select-then-insert race conditions.
        """
        edge_id = key_to_uuid(we.key)
        source_id = key_to_uuid(we.source_node_key)
        target_id = key_to_uuid(we.target_node_key)

        # Verify both nodes exist in graph-db
        src_exists = (await gs.execute(select(Node.id).where(Node.id == source_id))).scalar_one_or_none()
        tgt_exists = (await gs.execute(select(Node.id).where(Node.id == target_id))).scalar_one_or_none()
        if src_exists is None or tgt_exists is None:
            logger.debug("Skipping edge %s: source or target node not yet synced", we.key)
            return

        # Canonical ordering for undirected edges in graph-db.
        # Directed edges (e.g. draws_from) preserve their original order.
        from kt_config.types import UNDIRECTED_EDGE_TYPES

        if we.relationship_type in UNDIRECTED_EDGE_TYPES and target_id < source_id:
            source_id, target_id = target_id, source_id

        now = datetime.now(UTC).replace(tzinfo=None)
        values: dict = {
            "id": edge_id,
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relationship_type": we.relationship_type,
            "weight": we.weight,
            "justification": we.justification,
            "weight_source": we.weight_source,
            "metadata": we.metadata_,
        }

        stmt = (
            pg_insert(Edge.__table__)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_edge_source_target_type",
                set_={
                    "weight": we.weight,
                    "justification": we.justification,
                    "weight_source": we.weight_source,
                    "metadata": we.metadata_,
                    "updated_at": now,
                },
            )
            .returning(Edge.__table__.c.id)
        )
        result = await gs.execute(stmt)
        actual_edge_id = result.scalar_one()

        # Create EdgeFact junction rows from stored fact_ids.
        # Use actual_edge_id (may differ from edge_id if row already existed).
        # Inner savepoint protects enclosing transaction from FK violations.
        if we.fact_ids:
            for fid_str in we.fact_ids:
                try:
                    fact_id = uuid.UUID(fid_str)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(EdgeFact.__table__)
                            .values(edge_id=actual_edge_id, fact_id=fact_id, relevance_score=1.0)
                            .on_conflict_do_nothing()
                        )
                        await gs.execute(stmt)
                except (ValueError, Exception):
                    logger.debug("Failed to link fact %s to edge %s", fid_str, actual_edge_id)

    # ── Dimensions ─────────────────────────────────────────────────────

    async def _sync_dimensions(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_dimensions")
            rows = (
                (
                    await ws.execute(
                        select(WriteDimension)
                        .where(WriteDimension.updated_at > watermark)
                        .order_by(WriteDimension.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d dimensions (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wd in rows:
                node_id = key_to_uuid(wd.node_key)

                # Verify node exists in graph-db
                node_exists = (await gs.execute(select(Node.id).where(Node.id == node_id))).scalar_one_or_none()
                if node_exists is None:
                    logger.warning(
                        "Skipping dimension %s (node_key=%s): node not yet synced to graph-db", wd.key, wd.node_key
                    )
                    continue

                dim_id = key_to_uuid(wd.key)

                try:
                    async with gs.begin_nested():
                        values: dict = {
                            "id": dim_id,
                            "node_id": node_id,
                            "model_id": wd.model_id,
                            "content": wd.content,
                            "confidence": wd.confidence,
                            "suggested_concepts": wd.suggested_concepts,
                            "batch_index": wd.batch_index,
                            "fact_count": wd.fact_count,
                            "is_definitive": wd.is_definitive,
                        }
                        stmt = (
                            pg_insert(Dimension.__table__)
                            .values(**values)
                            .on_conflict_do_update(
                                index_elements=[Dimension.__table__.c.id],
                                set_={
                                    "content": wd.content,
                                    "confidence": wd.confidence,
                                    "suggested_concepts": wd.suggested_concepts,
                                    "fact_count": wd.fact_count,
                                    "is_definitive": wd.is_definitive,
                                },
                            )
                        )
                        await gs.execute(stmt)

                        # Create DimensionFact junction rows from stored fact_ids
                        # Inner savepoint protects the dimension upsert above.
                        if wd.fact_ids:
                            for fid_str in wd.fact_ids:
                                try:
                                    fact_id = uuid.UUID(fid_str)
                                    async with gs.begin_nested():
                                        stmt = (
                                            pg_insert(DimensionFact.__table__)
                                            .values(dimension_id=dim_id, fact_id=fact_id)
                                            .on_conflict_do_nothing()
                                        )
                                        await gs.execute(stmt)
                                except (ValueError, Exception):
                                    logger.debug("Failed to link fact %s to dimension %s", fid_str, dim_id)
                except Exception as exc:
                    logger.error("Failed to sync dimension %s, skipping", wd.key, exc_info=True)
                    if first_failure_ts is None:
                        first_failure_ts = wd.updated_at
                    await self._record_failure(ws, "write_dimensions", wd.key, exc)
                    continue

                if wd.updated_at > max_ts:
                    max_ts = wd.updated_at
                count += 1

            # Refresh dimension_count on affected nodes
            dim_node_ids = {key_to_uuid(wd.node_key) for wd in rows}
            await self._refresh_node_stats(gs, dim_node_ids)

            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Dimensions: %d/%d synced, %d failed — watermark frozen at %s (first failure)",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_dimensions", safe_ts)
            await ws.commit()
            return count

    # ── Convergence Reports ────────────────────────────────────────────

    async def _sync_convergence(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_convergence_reports")
            rows = (
                (
                    await ws.execute(
                        select(WriteConvergenceReport)
                        .where(WriteConvergenceReport.updated_at > watermark)
                        .order_by(WriteConvergenceReport.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d convergence reports (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wcr in rows:
                node_id = key_to_uuid(wcr.node_key)

                node_exists = (await gs.execute(select(Node.id).where(Node.id == node_id))).scalar_one_or_none()
                if node_exists is None:
                    logger.debug(
                        "Skipping convergence for node_key=%s: node not yet synced",
                        wcr.node_key,
                    )
                    continue

                try:
                    async with gs.begin_nested():
                        # ConvergenceReport has unique(node_id), use that for upsert
                        stmt = (
                            pg_insert(ConvergenceReport.__table__)
                            .values(
                                id=uuid.uuid4(),
                                node_id=node_id,
                                convergence_score=wcr.convergence_score,
                                converged_claims=wcr.converged_claims,
                                recommended_content=wcr.recommended_content,
                            )
                            .on_conflict_do_update(
                                index_elements=[ConvergenceReport.__table__.c.node_id],
                                set_={
                                    "convergence_score": wcr.convergence_score,
                                    "converged_claims": wcr.converged_claims,
                                    "recommended_content": wcr.recommended_content,
                                },
                            )
                        )
                        await gs.execute(stmt)
                except Exception as exc:
                    logger.error(
                        "Failed to sync convergence for node %s, skipping",
                        wcr.node_key,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wcr.updated_at
                    await self._record_failure(ws, "write_convergence_reports", wcr.node_key, exc)
                    continue

                if wcr.updated_at > max_ts:
                    max_ts = wcr.updated_at
                count += 1

            # Refresh convergence_score on affected nodes
            conv_node_ids = {key_to_uuid(wcr.node_key) for wcr in rows}
            await self._refresh_node_stats(gs, conv_node_ids)

            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Convergence: %d/%d synced, %d failed — watermark frozen at %s",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_convergence_reports", safe_ts)
            await ws.commit()
            return count

    # ── Counters ───────────────────────────────────────────────────────

    async def _sync_counters(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_node_counters")
            rows = (
                (
                    await ws.execute(
                        select(WriteNodeCounter)
                        .where(WriteNodeCounter.updated_at > watermark)
                        .order_by(WriteNodeCounter.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d node counters (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wnc in rows:
                node_id = key_to_uuid(wnc.node_key)

                node_exists = (await gs.execute(select(Node.id).where(Node.id == node_id))).scalar_one_or_none()
                if node_exists is None:
                    logger.debug(
                        "Skipping counter for node_key=%s: node not yet synced",
                        wnc.node_key,
                    )
                    continue

                try:
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(NodeCounter)
                            .values(
                                node_id=node_id,
                                access_count=wnc.access_count,
                                update_count=wnc.update_count,
                                seed_fact_count=wnc.seed_fact_count,
                            )
                            .on_conflict_do_update(
                                index_elements=[NodeCounter.node_id],
                                set_={
                                    "access_count": wnc.access_count,
                                    "update_count": wnc.update_count,
                                    "seed_fact_count": wnc.seed_fact_count,
                                },
                            )
                        )
                        await gs.execute(stmt)
                except Exception as exc:
                    logger.error(
                        "Failed to sync counter for node %s, skipping",
                        wnc.node_key,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wnc.updated_at
                    await self._record_failure(ws, "write_node_counters", wnc.node_key, exc)
                    continue

                if wnc.updated_at > max_ts:
                    max_ts = wnc.updated_at
                count += 1

            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Counters: %d/%d synced, %d failed — watermark frozen at %s",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_node_counters", safe_ts)
            await ws.commit()
            return count

    # ── Node Versions ───────────────────────────────────────────────

    async def _sync_node_versions(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_node_versions")
            rows = (
                (
                    await ws.execute(
                        select(WriteNodeVersion)
                        .where(WriteNodeVersion.updated_at > watermark)
                        .order_by(WriteNodeVersion.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d node versions (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wnv in rows:
                node_id = key_to_uuid(wnv.node_key)

                node_exists = (await gs.execute(select(Node.id).where(Node.id == node_id))).scalar_one_or_none()
                if node_exists is None:
                    logger.debug("Skipping node version %s: node not yet synced", wnv.node_key)
                    continue

                try:
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(NodeVersion.__table__)
                            .values(
                                id=wnv.id,
                                node_id=node_id,
                                version_number=wnv.version_number,
                                snapshot=wnv.snapshot,
                                source_node_count=wnv.source_node_count,
                                is_default=wnv.is_default,
                                created_at=wnv.created_at,
                            )
                            .on_conflict_do_update(
                                index_elements=[NodeVersion.__table__.c.id],
                                set_={
                                    "snapshot": wnv.snapshot,
                                    "source_node_count": wnv.source_node_count,
                                    "is_default": wnv.is_default,
                                },
                            )
                        )
                        await gs.execute(stmt)
                except Exception as exc:
                    logger.error(
                        "Failed to sync node version %s v%d, skipping",
                        wnv.node_key,
                        wnv.version_number,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wnv.updated_at
                    await self._record_failure(ws, "write_node_versions", wnv.node_key, exc)
                    continue

                if wnv.updated_at > max_ts:
                    max_ts = wnv.updated_at
                count += 1

            safe_ts = min(max_ts, first_failure_ts) if first_failure_ts else max_ts
            if first_failure_ts is not None:
                failed = len(rows) - count
                logger.warning(
                    "Node versions: %d/%d synced, %d failed — watermark frozen at %s",
                    count,
                    len(rows),
                    failed,
                    safe_ts.isoformat(),
                )
            await gs.commit()
            await self._set_watermark(ws, "write_node_versions", safe_ts)
            await ws.commit()
            return count

    # ── LLM Usage ──────────────────────────────────────────────────────

    async def _sync_llm_usage(self) -> int:
        async with self._write_sf() as ws, self._graph_sf() as gs:
            watermark = await self._get_watermark(ws, "write_llm_usage")
            rows = (
                (
                    await ws.execute(
                        select(WriteLlmUsage)
                        .where(WriteLlmUsage.updated_at > watermark)
                        .order_by(WriteLlmUsage.updated_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Syncing %d llm_usage records (watermark=%s)",
                len(rows),
                watermark.isoformat(),
            )

            max_ts = watermark
            count = 0
            first_failure_ts: datetime | None = None
            for wu in rows:
                try:
                    # Parse conversation_id / message_id as UUIDs for graph-db
                    conv_uuid = _safe_uuid(wu.conversation_id)
                    msg_uuid = _safe_uuid(wu.message_id)

                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(LlmUsage.__table__)
                            .values(
                                id=wu.id,
                                conversation_id=conv_uuid,
                                message_id=msg_uuid,
                                task_type=wu.task_type,
                                workflow_run_id=wu.workflow_run_id,
                                model_id=wu.model_id,
                                prompt_tokens=wu.prompt_tokens,
                                completion_tokens=wu.completion_tokens,
                                cost_usd=wu.cost_usd,
                                created_at=wu.created_at,
                            )
                            .on_conflict_do_nothing(index_elements=["id"])
                        )
                        await gs.execute(stmt)

                    count += 1
                    if wu.updated_at > max_ts:
                        max_ts = wu.updated_at
                except Exception as exc:
                    logger.warning(
                        "Failed to sync llm_usage %s",
                        wu.id,
                        exc_info=True,
                    )
                    if first_failure_ts is None:
                        first_failure_ts = wu.updated_at
                    await self._record_failure(ws, "write_llm_usage", str(wu.id), exc)

            safe_ts = first_failure_ts if first_failure_ts is not None else max_ts
            if safe_ts > watermark:
                await self._set_watermark(ws, "write_llm_usage", safe_ts)
            await ws.commit()
            await gs.commit()
            return count

    # ── Dead-Letter Queue helpers ──────────────────────────────────────

    async def _record_failure(
        self,
        ws: AsyncSession,
        table_name: str,
        record_key: str,
        exc: Exception,
    ) -> None:
        """Record or update a sync failure in the dead-letter queue.

        Uses upsert keyed on (table_name, record_key) so repeated failures
        for the same record increment retry_count and push out next_retry_at
        with exponential backoff.
        """
        settings = get_settings()
        error_msg = f"{type(exc).__name__}: {exc}"
        now = datetime.now(UTC).replace(tzinfo=None)

        # Check if there's an existing failure for this record
        result = await ws.execute(
            select(SyncFailure)
            .where(SyncFailure.table_name == table_name)
            .where(SyncFailure.record_key == record_key)
            .where(SyncFailure.status == "pending")
            .limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            new_count = existing.retry_count + 1
            if new_count >= settings.sync_max_retries:
                status = "abandoned"
                next_retry = now  # won't be retried
                logger.warning(
                    "Abandoning sync for %s/%s after %d retries",
                    table_name,
                    record_key,
                    new_count,
                )
            else:
                status = "pending"
                backoff = settings.sync_retry_base_seconds * (2**new_count)
                next_retry = now + timedelta(seconds=backoff)

            await ws.execute(
                update(SyncFailure)
                .where(SyncFailure.id == existing.id)
                .values(
                    retry_count=new_count,
                    error_message=error_msg,
                    status=status,
                    next_retry_at=next_retry,
                    updated_at=now,
                )
            )
        else:
            backoff = settings.sync_retry_base_seconds * 2  # first retry: 2x base
            stmt = pg_insert(SyncFailure.__table__).values(
                table_name=table_name,
                record_key=record_key,
                error_message=error_msg,
                retry_count=1,
                status="pending",
                next_retry_at=now + timedelta(seconds=backoff),
            )
            await ws.execute(stmt)

    async def _clear_failure(self, ws: AsyncSession, table_name: str, record_key: str) -> None:
        """Remove a failure record after successful retry."""
        await ws.execute(
            delete(SyncFailure).where(SyncFailure.table_name == table_name).where(SyncFailure.record_key == record_key)
        )

    async def _retry_failed_syncs(self) -> int:
        """Retry pending sync failures whose backoff period has elapsed."""
        now = datetime.now(UTC).replace(tzinfo=None)

        async with self._write_sf() as ws:
            rows = (
                (
                    await ws.execute(
                        select(SyncFailure)
                        .where(SyncFailure.status == "pending")
                        .where(SyncFailure.next_retry_at <= now)
                        .order_by(SyncFailure.next_retry_at.asc())
                        .limit(self._batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return 0

            logger.info(
                "Retrying %d failed sync records from dead-letter queue",
                len(rows),
            )

            retried = 0
            for failure in rows:
                success = await self._retry_one(failure)
                if success:
                    await self._clear_failure(ws, failure.table_name, failure.record_key)
                    retried += 1
                    logger.info(
                        "Successfully retried sync for %s/%s",
                        failure.table_name,
                        failure.record_key,
                    )
            await ws.commit()
            return retried

    async def _retry_one(self, failure: SyncFailure) -> bool:
        """Attempt to re-sync a single failed record. Returns True on success."""
        table = failure.table_name
        key = failure.record_key

        try:
            async with self._write_sf() as ws, self._graph_sf() as gs:
                if table == "write_facts":
                    fact_id = uuid.UUID(key)
                    wf = (await ws.execute(select(WriteFact).where(WriteFact.id == fact_id))).scalar_one_or_none()
                    if wf is None:
                        return True  # source record gone, clear failure
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(Fact.__table__)
                            .values(id=wf.id, content=wf.content, fact_type=wf.fact_type, metadata=wf.metadata_)
                            .on_conflict_do_update(
                                index_elements=[Fact.__table__.c.id],
                                set_={"content": wf.content, "fact_type": wf.fact_type, "metadata": wf.metadata_},
                            )
                        )
                        await gs.execute(stmt)
                        fact_sources = (
                            (await ws.execute(select(WriteFactSource).where(WriteFactSource.fact_id == wf.id)))
                            .scalars()
                            .all()
                        )
                        for wfs in fact_sources:
                            await self._sync_one_fact_source(ws, gs, wf.id, wfs)
                    await gs.commit()
                    return True

                elif table == "write_nodes":
                    wn = (await ws.execute(select(WriteNode).where(WriteNode.key == key))).scalar_one_or_none()
                    if wn is None:
                        return True
                    # Load rejected facts for this node
                    _rej_rows = (
                        (
                            await ws.execute(
                                select(WriteNodeFactRejection.fact_id).where(
                                    WriteNodeFactRejection.node_id == wn.node_uuid
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    _rej_fids = {str(fid) for fid in _rej_rows}
                    try:
                        async with gs.begin_nested():
                            await self._upsert_graph_node(
                                gs,
                                wn,
                                skip_parent=False,
                                rejected_fact_ids=_rej_fids,
                            )
                    except IntegrityError:
                        # Parent node not yet in graph-db — sync without parent FK;
                        # _repair_orphaned_node_refs will resolve it on the next cycle.
                        logger.warning(
                            "Retry: parent FK missing for node %s, syncing without parent",
                            wn.key,
                        )
                        async with gs.begin_nested():
                            await self._upsert_graph_node(
                                gs,
                                wn,
                                skip_parent=True,
                                rejected_fact_ids=_rej_fids,
                            )
                    await gs.commit()
                    return True

                elif table == "write_edges":
                    we = (await ws.execute(select(WriteEdge).where(WriteEdge.key == key))).scalar_one_or_none()
                    if we is None:
                        return True
                    async with gs.begin_nested():
                        await self._upsert_graph_edge(gs, we)
                    await gs.commit()
                    return True

                elif table == "write_dimensions":
                    wd = (
                        await ws.execute(select(WriteDimension).where(WriteDimension.key == key))
                    ).scalar_one_or_none()
                    if wd is None:
                        return True
                    node_id = key_to_uuid(wd.node_key)
                    dim_id = key_to_uuid(wd.key)
                    async with gs.begin_nested():
                        values: dict = {
                            "id": dim_id,
                            "node_id": node_id,
                            "model_id": wd.model_id,
                            "content": wd.content,
                            "confidence": wd.confidence,
                            "suggested_concepts": wd.suggested_concepts,
                            "batch_index": wd.batch_index,
                            "fact_count": wd.fact_count,
                            "is_definitive": wd.is_definitive,
                        }
                        stmt = (
                            pg_insert(Dimension.__table__)
                            .values(**values)
                            .on_conflict_do_update(
                                index_elements=[Dimension.__table__.c.id],
                                set_={
                                    "content": wd.content,
                                    "confidence": wd.confidence,
                                    "suggested_concepts": wd.suggested_concepts,
                                    "fact_count": wd.fact_count,
                                    "is_definitive": wd.is_definitive,
                                },
                            )
                        )
                        await gs.execute(stmt)
                        if wd.fact_ids:
                            for fid_str in wd.fact_ids:
                                try:
                                    fact_id = uuid.UUID(fid_str)
                                    async with gs.begin_nested():
                                        await gs.execute(
                                            pg_insert(DimensionFact.__table__)
                                            .values(dimension_id=dim_id, fact_id=fact_id)
                                            .on_conflict_do_nothing()
                                        )
                                except (ValueError, Exception):
                                    pass
                    await gs.commit()
                    return True

                elif table == "write_convergence_reports":
                    wcr = (
                        await ws.execute(select(WriteConvergenceReport).where(WriteConvergenceReport.node_key == key))
                    ).scalar_one_or_none()
                    if wcr is None:
                        return True
                    node_id = key_to_uuid(wcr.node_key)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(ConvergenceReport.__table__)
                            .values(
                                id=uuid.uuid4(),
                                node_id=node_id,
                                convergence_score=wcr.convergence_score,
                                converged_claims=wcr.converged_claims,
                                recommended_content=wcr.recommended_content,
                            )
                            .on_conflict_do_update(
                                index_elements=[ConvergenceReport.__table__.c.node_id],
                                set_={
                                    "convergence_score": wcr.convergence_score,
                                    "converged_claims": wcr.converged_claims,
                                    "recommended_content": wcr.recommended_content,
                                },
                            )
                        )
                        await gs.execute(stmt)
                    await gs.commit()
                    return True

                elif table == "write_node_counters":
                    wnc = (
                        await ws.execute(select(WriteNodeCounter).where(WriteNodeCounter.node_key == key))
                    ).scalar_one_or_none()
                    if wnc is None:
                        return True
                    node_id = key_to_uuid(wnc.node_key)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(NodeCounter)
                            .values(node_id=node_id, access_count=wnc.access_count, update_count=wnc.update_count)
                            .on_conflict_do_update(
                                index_elements=[NodeCounter.node_id],
                                set_={"access_count": wnc.access_count, "update_count": wnc.update_count},
                            )
                        )
                        await gs.execute(stmt)
                    await gs.commit()
                    return True

                elif table == "write_node_versions":
                    wnv = (
                        await ws.execute(select(WriteNodeVersion).where(WriteNodeVersion.node_key == key))
                    ).scalar_one_or_none()
                    if wnv is None:
                        return True
                    node_id = key_to_uuid(wnv.node_key)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(NodeVersion.__table__)
                            .values(
                                id=wnv.id,
                                node_id=node_id,
                                version_number=wnv.version_number,
                                snapshot=wnv.snapshot,
                                source_node_count=wnv.source_node_count,
                                is_default=wnv.is_default,
                                created_at=wnv.created_at,
                            )
                            .on_conflict_do_update(
                                index_elements=[NodeVersion.__table__.c.id],
                                set_={
                                    "snapshot": wnv.snapshot,
                                    "source_node_count": wnv.source_node_count,
                                    "is_default": wnv.is_default,
                                },
                            )
                        )
                        await gs.execute(stmt)
                    await gs.commit()
                    return True

                elif table == "write_llm_usage":
                    usage_id = uuid.UUID(key)
                    wu = (
                        await ws.execute(select(WriteLlmUsage).where(WriteLlmUsage.id == usage_id))
                    ).scalar_one_or_none()
                    if wu is None:
                        return True
                    conv_uuid = _safe_uuid(wu.conversation_id)
                    msg_uuid = _safe_uuid(wu.message_id)
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(LlmUsage.__table__)
                            .values(
                                id=wu.id,
                                conversation_id=conv_uuid,
                                message_id=msg_uuid,
                                task_type=wu.task_type,
                                workflow_run_id=wu.workflow_run_id,
                                model_id=wu.model_id,
                                prompt_tokens=wu.prompt_tokens,
                                completion_tokens=wu.completion_tokens,
                                cost_usd=wu.cost_usd,
                                created_at=wu.created_at,
                            )
                            .on_conflict_do_nothing(index_elements=["id"])
                        )
                        await gs.execute(stmt)
                    await gs.commit()
                    return True

                elif table == "write_raw_sources":
                    src_id = uuid.UUID(key)
                    wrs = (
                        await ws.execute(select(WriteRawSource).where(WriteRawSource.id == src_id))
                    ).scalar_one_or_none()
                    if wrs is None:
                        return True
                    async with gs.begin_nested():
                        stmt = (
                            pg_insert(RawSource.__table__)
                            .values(
                                id=wrs.id,
                                uri=wrs.uri,
                                title=wrs.title,
                                raw_content=wrs.raw_content,
                                content_hash=wrs.content_hash,
                                is_full_text=wrs.is_full_text,
                                is_super_source=wrs.is_super_source,
                                content_type=wrs.content_type,
                                provider_id=wrs.provider_id,
                                provider_metadata=wrs.provider_metadata,
                                fact_count=wrs.fact_count,
                                prohibited_chunk_count=wrs.prohibited_chunk_count,
                                fetch_attempted=wrs.fetch_attempted,
                                fetch_error=wrs.fetch_error,
                            )
                            .on_conflict_do_update(
                                index_elements=["id"],
                                set_={
                                    "uri": wrs.uri,
                                    "title": wrs.title,
                                    "raw_content": wrs.raw_content,
                                    "content_hash": wrs.content_hash,
                                    "is_full_text": wrs.is_full_text,
                                    "is_super_source": wrs.is_super_source,
                                    "content_type": wrs.content_type,
                                    "provider_metadata": wrs.provider_metadata,
                                    # fact_count intentionally excluded — managed
                                    # by _sync_one_fact_source increments only.
                                    "prohibited_chunk_count": wrs.prohibited_chunk_count,
                                    "fetch_attempted": wrs.fetch_attempted,
                                    "fetch_error": wrs.fetch_error,
                                },
                            )
                        )
                        await gs.execute(stmt)
                    await gs.commit()
                    return True

                else:
                    logger.warning("Unknown table in sync_failures: %s", table)
                    return False
        except Exception:
            logger.error(
                "Retry failed for %s/%s",
                table,
                key,
                exc_info=True,
            )
            return False
