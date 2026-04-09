"""Cross-graph public-cache bridge.

The :class:`PublicGraphBridge` connects a private graph to the
"default" public graph so that source decompositions can be:

* **Looked up** in the public graph by canonical URL or DOI before being
  decomposed locally — saving expensive LLM cost when another graph has
  already processed the same source.
* **Imported** into the local graph (raw source + facts + concept/entity
  nodes) when a hit is found.
* **Contributed** upstream to the public graph after a local
  decomposition completes, so the public fact pool grows monotonically
  with use.

Design constraints (do NOT relax without consensus):

* The bridge writes **write-db only** on both sides. Workers never touch
  graph-db; the sync worker propagates from write-db to graph-db.
* The bridge takes explicit dependencies (resolver, qdrant, embedding
  service) — it never reaches back into a ``WorkerGraphEngine``. The
  engine *wraps* the bridge for ergonomic call sites, but the bridge
  itself is independently testable.
* Cross-session safety: ``CachedSourceImport`` is a **plain dataclass**
  carrying detached snapshots, never live ORM rows. The lookup session
  is closed before the data is handed to the import phase, which uses
  the target write-session.
* All bridge entry points must degrade gracefully — failures log and
  return ``None`` / no-op rather than aborting the surrounding ingest
  pipeline. The cache is an optimisation, not a load-bearing path.

PR4 ships the read-side (``lookup_cached_source``) and the contribute
path. The full Qdrant copy + concept-similarity branch in
``import_cached_source`` is implemented end-to-end here but only
exercised by unit tests; the workflow wiring lands in PR5.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from kt_db.graph_sessions import GraphSessionResolver
from kt_db.write_models import WriteFact, WriteFactSource, WriteNode, WriteRawSource

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from kt_config.settings import Settings
    from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detached snapshot dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedRawSourceSnapshot:
    """Detached copy of a ``WriteRawSource`` row from the public graph."""

    id: uuid.UUID
    uri: str
    canonical_url: str | None
    doi: str | None
    title: str | None
    raw_content: str | None
    content_hash: str
    content_type: str | None
    provider_id: str
    is_full_text: bool
    is_super_source: bool
    fact_count: int
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class CachedFactSnapshot:
    """A fact + its embedding (for re-dedup against the target Qdrant)."""

    id: uuid.UUID
    content: str
    fact_type: str
    metadata_: dict | None
    embedding: list[float] | None


@dataclass(frozen=True)
class CachedFactSourceSnapshot:
    """A fact-source provenance row attached to a cached fact."""

    fact_id: uuid.UUID
    raw_source_uri: str
    raw_source_title: str | None
    raw_source_content_hash: str
    raw_source_provider_id: str
    context_snippet: str | None
    attribution: str | None
    author_person: str | None
    author_org: str | None


@dataclass(frozen=True)
class CachedNodeSnapshot:
    """A concept/entity node with its embedding for similarity matching."""

    key: str
    node_uuid: uuid.UUID
    concept: str
    node_type: str
    definition: str | None
    embedding: list[float] | None
    fact_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class CachedSourceImport:
    """Everything ``import_cached_source`` needs from the public graph.

    Returned by :meth:`PublicGraphBridge.lookup_cached_source` and consumed
    by :meth:`PublicGraphBridge.import_cached_source`. Plain data — no live
    ORM rows, no open sessions — so it can cross worker boundaries safely.
    """

    raw_source: CachedRawSourceSnapshot
    facts: list[CachedFactSnapshot]
    fact_sources: list[CachedFactSourceSnapshot]
    nodes: list[CachedNodeSnapshot]
    is_stale: bool = False  # ``retrieved_at`` older than refresh threshold


@dataclass
class ImportResult:
    """Outcome of :meth:`PublicGraphBridge.import_cached_source`."""

    raw_source_id: uuid.UUID
    facts_imported: int = 0
    facts_deduped: int = 0
    nodes_matched: int = 0
    nodes_created: int = 0


# ---------------------------------------------------------------------------
# PublicGraphBridge
# ---------------------------------------------------------------------------


class PublicGraphBridge:
    """Cross-graph cache lookup / import / contribute helper.

    One instance is created per ``WorkerGraphEngine`` that targets a
    *non-default* graph. Workers never construct this directly — see
    :class:`kt_hatchet.lifespan.WorkerState` for the factory.
    """

    def __init__(
        self,
        resolver: GraphSessionResolver,
        qdrant_client: "AsyncQdrantClient | None",
        embedding_service: "EmbeddingService | None",
        default_graph_id: uuid.UUID,
        settings: "Settings",
    ) -> None:
        self._resolver = resolver
        self._qdrant_client = qdrant_client
        self._embedding_service = embedding_service
        self._default_graph_id = default_graph_id
        self._settings = settings
        self._refresh_after = timedelta(days=settings.public_cache_refresh_after_days)
        self._concept_threshold = settings.public_bridge_concept_match_threshold

    @property
    def default_graph_id(self) -> uuid.UUID:
        return self._default_graph_id

    # ── Lookup ────────────────────────────────────────────────────────

    async def lookup_cached_source(
        self,
        *,
        canonical_url: str | None,
        doi: str | None,
    ) -> CachedSourceImport | None:
        """Find a cached decomposition of this source in the public graph.

        Looks up ``write_raw_sources`` in the *default* graph by
        ``canonical_url`` OR ``doi`` (whichever is non-null), then eagerly
        snapshots the linked facts (via ``write_fact_sources.raw_source_uri``
        match) and any concept/entity nodes that link to those facts.

        Returns ``None`` when nothing matches or when the public graph is
        unreachable. Failures are logged and swallowed — the caller falls
        back to a normal local decomposition.
        """
        if not canonical_url and not doi:
            return None
        if self._qdrant_client is None:
            # Without Qdrant we can't carry embeddings forward; the bridge
            # is useless. Don't pretend otherwise.
            logger.debug("PublicGraphBridge: no Qdrant client; lookup disabled")
            return None

        try:
            gs = await self._resolver.resolve(self._default_graph_id)
        except Exception:
            logger.warning("PublicGraphBridge: cannot resolve default graph", exc_info=True)
            return None

        try:
            async with gs.write_session_factory() as session:
                row = await self._find_raw_source(session, canonical_url, doi)
                if row is None:
                    logger.info(
                        "public_cache.miss canonical_url=%s doi=%s",
                        canonical_url,
                        doi,
                    )
                    return None
                snapshot = _snapshot_raw_source(row)

                fact_rows = await self._load_linked_facts(session, row)
                fact_source_rows = await self._load_linked_fact_sources(session, row)
                node_rows = await self._load_linked_nodes(session, [f.id for f in fact_rows])
        except Exception:
            logger.warning(
                "public_cache.lookup_fail canonical_url=%s doi=%s",
                canonical_url,
                doi,
                exc_info=True,
            )
            return None

        # Pull embeddings from the *public* graph's Qdrant collections.
        public_prefix = gs.qdrant_collection_prefix
        fact_embeddings = await self._fetch_qdrant_vectors(
            collection=f"{public_prefix}facts",
            ids=[f.id for f in fact_rows],
        )
        node_embeddings = await self._fetch_qdrant_vectors(
            collection=f"{public_prefix}nodes",
            ids=[n.node_uuid for n in node_rows],
        )

        facts = [_snapshot_fact(f, fact_embeddings.get(f.id)) for f in fact_rows]
        fact_sources = [_snapshot_fact_source(fs) for fs in fact_source_rows]
        nodes = [_snapshot_node(n, node_embeddings.get(n.node_uuid)) for n in node_rows]

        is_stale = self._is_stale(snapshot.retrieved_at)
        logger.info(
            "public_cache.hit canonical_url=%s doi=%s facts=%d nodes=%d stale=%s",
            canonical_url,
            doi,
            len(facts),
            len(nodes),
            is_stale,
        )
        return CachedSourceImport(
            raw_source=snapshot,
            facts=facts,
            fact_sources=fact_sources,
            nodes=nodes,
            is_stale=is_stale,
        )

    # ── Import ────────────────────────────────────────────────────────

    async def import_cached_source(
        self,
        import_data: CachedSourceImport,
        *,
        target_write_session: "AsyncSession",
        target_qdrant_prefix: str,
    ) -> ImportResult:
        """Write a cached snapshot into the *target* (private) graph.

        Steps:

        1. Upsert ``write_raw_sources`` (idempotent on ``content_hash``).
        2. For each fact, dedup against the target Qdrant fact collection.
           On hit reuse the local fact id; on miss create a new
           ``write_facts`` row + push the embedding to the target
           collection.
        3. Mirror ``write_fact_sources`` rows for the imported facts.
        4. For each cached concept/entity node, search the target node
           collection for cosine ≥ ``public_bridge_concept_match_threshold``.
           On match reuse the local node id (no row created — the local
           node already exists). On miss, create a new ``write_nodes`` row
           with a deterministic key and push its embedding to the target
           node collection.

        Returns an :class:`ImportResult` with counts. Errors propagate so
        the caller can fall back to local decomposition; on partial
        failure the surrounding session rollback unwinds DB writes, but
        Qdrant points are not transactional so we keep a list and best-
        effort delete them in a finally block.
        """
        result = ImportResult(raw_source_id=import_data.raw_source.id)
        new_qdrant_fact_ids: list[uuid.UUID] = []
        new_qdrant_node_ids: list[uuid.UUID] = []

        try:
            local_raw_id = await self._upsert_raw_source(target_write_session, import_data.raw_source)
            result.raw_source_id = local_raw_id

            # ── Facts: dedup against target Qdrant ─────────────────────
            fact_collection = f"{target_qdrant_prefix}facts"
            local_fact_id_by_remote: dict[uuid.UUID, uuid.UUID] = {}
            for fact in import_data.facts:
                local_id = await self._dedup_or_create_fact(
                    target_write_session,
                    fact,
                    collection=fact_collection,
                )
                local_fact_id_by_remote[fact.id] = local_id
                if local_id == fact.id:
                    # Brand new — Qdrant point was just inserted.
                    new_qdrant_fact_ids.append(local_id)
                    result.facts_imported += 1
                else:
                    result.facts_deduped += 1

            # ── Fact sources ───────────────────────────────────────────
            for fs in import_data.fact_sources:
                local_fact_id = local_fact_id_by_remote.get(fs.fact_id)
                if local_fact_id is None:
                    continue
                await self._upsert_fact_source(target_write_session, fs, local_fact_id)

            # ── Nodes: concept-similarity match or create ──────────────
            node_collection = f"{target_qdrant_prefix}nodes"
            for node in import_data.nodes:
                matched = await self._match_or_create_node(
                    target_write_session,
                    node,
                    collection=node_collection,
                    local_fact_id_by_remote=local_fact_id_by_remote,
                )
                if matched.created:
                    result.nodes_created += 1
                    new_qdrant_node_ids.append(matched.local_node_id)
                else:
                    result.nodes_matched += 1
        except Exception:
            # Compensating Qdrant deletes — DB rollback is the caller's job.
            await self._delete_qdrant_points(f"{target_qdrant_prefix}facts", new_qdrant_fact_ids)
            await self._delete_qdrant_points(f"{target_qdrant_prefix}nodes", new_qdrant_node_ids)
            raise

        return result

    # ── Contribute ────────────────────────────────────────────────────

    async def contribute_source_and_facts(
        self,
        *,
        raw_source_id: uuid.UUID,
        source_write_session: "AsyncSession",
        source_qdrant_prefix: str,
    ) -> None:
        """Push a freshly-decomposed source + its facts upstream.

        Reads the source and its facts from the *source* (private) graph,
        opens a write session on the *default* graph via the resolver, and
        upserts the raw source + dedup'd facts there. **Nodes are NOT
        contributed** — only sources and facts flow upstream. The public
        graph builds its own node structure from the accumulated fact pool
        on its own pipeline runs.

        All errors are logged at WARNING and swallowed; contribution is
        best-effort and never aborts the surrounding ingest workflow.
        """
        try:
            source_row = await source_write_session.get(WriteRawSource, raw_source_id)
            if source_row is None:
                logger.warning("contribute: raw_source %s not found in source graph", raw_source_id)
                return
            source_snapshot = _snapshot_raw_source(source_row)

            fact_rows = await self._facts_for_source(source_write_session, source_row)
            fact_source_rows = await self._fact_sources_for_source(source_write_session, source_row)
        except Exception:
            logger.warning("contribute: snapshot phase failed for %s", raw_source_id, exc_info=True)
            return

        # Load embeddings from the *source* Qdrant prefix.
        fact_embeddings = await self._fetch_qdrant_vectors(
            collection=f"{source_qdrant_prefix}facts",
            ids=[f.id for f in fact_rows],
        )
        fact_snapshots = [_snapshot_fact(f, fact_embeddings.get(f.id)) for f in fact_rows]
        fact_source_snapshots = [_snapshot_fact_source(fs) for fs in fact_source_rows]

        try:
            gs = await self._resolver.resolve(self._default_graph_id)
        except Exception:
            logger.warning("contribute: cannot resolve default graph", exc_info=True)
            return

        try:
            async with gs.write_session_factory() as target_session:
                async with target_session.begin():
                    # Return value intentionally discarded — contribute
                    # only cares that the source landed; downstream rows
                    # are keyed on ``content_hash`` denormalised onto each
                    # write_fact_source row, not on the raw source id.
                    await self._upsert_raw_source(target_session, source_snapshot)

                    target_collection = f"{gs.qdrant_collection_prefix}facts"
                    local_fact_id_by_remote: dict[uuid.UUID, uuid.UUID] = {}
                    for fact in fact_snapshots:
                        local_id = await self._dedup_or_create_fact(
                            target_session,
                            fact,
                            collection=target_collection,
                        )
                        local_fact_id_by_remote[fact.id] = local_id

                    for fs in fact_source_snapshots:
                        local_fact_id = local_fact_id_by_remote.get(fs.fact_id)
                        if local_fact_id is None:
                            continue
                        await self._upsert_fact_source(target_session, fs, local_fact_id)
        except Exception:
            logger.warning(
                "public_cache.contribute_fail raw_source_id=%s facts=%d",
                raw_source_id,
                len(fact_snapshots),
                exc_info=True,
            )
            return

        # ── Stamp the watermark on the source side ──────────────────
        # Done in its own statement (and its own transaction context)
        # so the upstream write succeeds even if the source-side stamp
        # fails — the sweeper will retry the watermark on the next
        # sweep, but we never want a successful upstream write to
        # appear undone. Logged but never raised.
        try:
            await source_write_session.execute(
                sa.update(WriteRawSource)
                .where(WriteRawSource.id == raw_source_id)
                .values(contributed_to_public_at=datetime.now(UTC).replace(tzinfo=None))
            )
        except Exception:
            logger.warning(
                "public_cache.watermark_fail raw_source_id=%s — sweeper will retry stamp",
                raw_source_id,
                exc_info=True,
            )

        logger.info(
            "public_cache.contribute_ok raw_source_id=%s facts=%d",
            raw_source_id,
            len(fact_snapshots),
        )

    # ── Internal: SQL helpers ─────────────────────────────────────────

    async def _find_raw_source(
        self,
        session: "AsyncSession",
        canonical_url: str | None,
        doi: str | None,
    ) -> WriteRawSource | None:
        """Query the public-graph write-db for a matching raw source."""
        stmt = select(WriteRawSource).limit(1)
        if doi:
            stmt = stmt.where(WriteRawSource.doi == doi)
        else:
            stmt = stmt.where(WriteRawSource.canonical_url == canonical_url)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_linked_facts(self, session: "AsyncSession", source: WriteRawSource) -> list[WriteFact]:
        """Facts whose ``write_fact_sources`` row points at this raw source."""
        stmt = (
            select(WriteFact)
            .join(
                WriteFactSource,
                WriteFactSource.fact_id == WriteFact.id,
            )
            .where(WriteFactSource.raw_source_content_hash == source.content_hash)
            .distinct()
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_linked_fact_sources(self, session: "AsyncSession", source: WriteRawSource) -> list[WriteFactSource]:
        stmt = select(WriteFactSource).where(WriteFactSource.raw_source_content_hash == source.content_hash)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_linked_nodes(self, session: "AsyncSession", fact_ids: list[uuid.UUID]) -> list[WriteNode]:
        """Concept/entity nodes that link to any of the given facts.

        Uses the ``write_nodes.fact_ids`` array column with the PG ``&&``
        overlap operator. Limited to concept and entity types — perspectives
        and events have stance/timeline semantics that don't make sense to
        copy across graphs.
        """
        if not fact_ids:
            return []
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.types import String as SAString

        fact_id_strings = [str(f) for f in fact_ids]
        stmt = (
            select(WriteNode)
            .where(WriteNode.node_type.in_(("concept", "entity")))
            .where(WriteNode.fact_ids.op("&&")(cast(fact_id_strings, ARRAY(SAString))))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _facts_for_source(self, session: "AsyncSession", source: WriteRawSource) -> list[WriteFact]:
        return await self._load_linked_facts(session, source)

    async def _fact_sources_for_source(self, session: "AsyncSession", source: WriteRawSource) -> list[WriteFactSource]:
        return await self._load_linked_fact_sources(session, source)

    async def _upsert_raw_source(self, session: "AsyncSession", snapshot: CachedRawSourceSnapshot) -> uuid.UUID:
        """Idempotent upsert keyed on the unique ``content_hash`` index.

        We deliberately do NOT use ``id`` as the conflict target — different
        graphs may have generated different UUIDs for the same content
        before this PR landed. ``content_hash`` is the only stable identity.

        Returns the **local** row id. When the insert lands a new row this
        is the remote id we just wrote; when ON CONFLICT no-ops we re-look
        the existing local row by ``content_hash`` so callers (and the
        ImportResult counters) see the local identity, never the remote
        one.
        """
        stmt = (
            pg_insert(WriteRawSource)
            .values(
                id=snapshot.id,
                uri=snapshot.uri,
                canonical_url=snapshot.canonical_url,
                doi=snapshot.doi,
                title=snapshot.title,
                raw_content=snapshot.raw_content,
                content_hash=snapshot.content_hash,
                content_type=snapshot.content_type,
                provider_id=snapshot.provider_id,
                is_full_text=snapshot.is_full_text,
                is_super_source=snapshot.is_super_source,
                fact_count=snapshot.fact_count,
            )
            .on_conflict_do_nothing(index_elements=["content_hash"])
            .returning(WriteRawSource.id)
        )
        result = await session.execute(stmt)
        returned = result.scalar_one_or_none()
        if returned is not None:
            return returned
        # ON CONFLICT no-op: a row with this content_hash already exists.
        existing = await session.execute(
            select(WriteRawSource.id).where(WriteRawSource.content_hash == snapshot.content_hash)
        )
        existing_id = existing.scalar_one_or_none()
        return existing_id if existing_id is not None else snapshot.id

    async def _dedup_or_create_fact(
        self,
        session: "AsyncSession",
        fact: CachedFactSnapshot,
        *,
        collection: str,
    ) -> uuid.UUID:
        """Find an equivalent fact in the target Qdrant or create a new one.

        Threshold matches ``deduplicate_facts``: 0.92 atomic, 0.85 compound.
        Without an embedding we cannot dedup, so we just create the fact.
        """
        if fact.embedding is not None and self._qdrant_client is not None:
            from kt_config.types import COMPOUND_FACT_TYPES

            threshold = 0.85 if fact.fact_type in COMPOUND_FACT_TYPES else 0.92
            try:
                from kt_qdrant.repositories.facts import QdrantFactRepository

                repo = QdrantFactRepository(self._qdrant_client, collection)
                hit = await repo.find_most_similar(fact.embedding, score_threshold=threshold)
                if hit is not None:
                    return hit.fact_id
            except Exception:
                logger.debug("dedup search failed in %s, creating new fact", collection, exc_info=True)

        # Create new write-db fact under the *remote* UUID so the snapshot
        # round-trips cleanly. The remote graph chose this UUID via dedup
        # already, so reusing it just means our local row matches.
        stmt = (
            pg_insert(WriteFact)
            .values(
                id=fact.id,
                content=fact.content,
                fact_type=fact.fact_type,
                metadata_=fact.metadata_,
            )
            .on_conflict_do_nothing(index_elements=[WriteFact.id])
        )
        await session.execute(stmt)

        # Push embedding into the target collection so future cache imports
        # in this graph dedup against it.
        if fact.embedding is not None and self._qdrant_client is not None:
            try:
                from kt_qdrant.repositories.facts import QdrantFactRepository

                await QdrantFactRepository(self._qdrant_client, collection).upsert(
                    fact_id=fact.id,
                    embedding=fact.embedding,
                    fact_type=fact.fact_type,
                    content=fact.content,
                )
            except Exception:
                logger.debug("fact upsert into %s failed for %s", collection, fact.id, exc_info=True)

        return fact.id

    async def _upsert_fact_source(
        self,
        session: "AsyncSession",
        fs: CachedFactSourceSnapshot,
        local_fact_id: uuid.UUID,
    ) -> None:
        """Insert a fact-source row idempotently across re-imports.

        ``write_fact_sources`` has no unique constraint on
        ``(fact_id, raw_source_content_hash)``, so two imports of the same
        source into the same target graph would otherwise create duplicate
        provenance rows. We synthesize a deterministic UUID5 from
        ``(fact_id, content_hash)`` and use ON CONFLICT DO NOTHING on the
        ``id`` column to dedup at the row level — re-imports become true
        no-ops without needing a schema change. PR5 should still avoid
        re-imports at the workflow level, this is defence in depth.
        """
        new_id = uuid.uuid5(
            uuid.NAMESPACE_OID,
            f"write_fact_source:{local_fact_id}:{fs.raw_source_content_hash}",
        )
        stmt = (
            pg_insert(WriteFactSource)
            .values(
                id=new_id,
                fact_id=local_fact_id,
                raw_source_uri=fs.raw_source_uri,
                raw_source_title=fs.raw_source_title,
                raw_source_content_hash=fs.raw_source_content_hash,
                raw_source_provider_id=fs.raw_source_provider_id,
                context_snippet=fs.context_snippet,
                attribution=fs.attribution,
                author_person=fs.author_person,
                author_org=fs.author_org,
                # Imported sources are visible to every member of the
                # private graph — see the plan's "Resolved decisions" note.
                access_groups=None,
            )
            .on_conflict_do_nothing(index_elements=[WriteFactSource.id])
        )
        await session.execute(stmt)

    @dataclass
    class _NodeMatchOutcome:
        local_node_id: uuid.UUID
        created: bool

    async def _match_or_create_node(
        self,
        session: "AsyncSession",
        node: CachedNodeSnapshot,
        *,
        collection: str,
        local_fact_id_by_remote: dict[uuid.UUID, uuid.UUID],
    ) -> "PublicGraphBridge._NodeMatchOutcome":
        """Find a similar local node or create a new one.

        Concept similarity is the cross-graph bridge's only "fuzzy" join —
        the rest of the bridge is identity-based. We use a high default
        threshold (``public_bridge_concept_match_threshold = 0.93``) because
        a false match collapses two distinct concepts together, which is
        far worse than the cost of an occasional duplicate that the local
        dedup pipeline will merge later anyway.
        """
        if node.embedding is not None and self._qdrant_client is not None:
            try:
                from kt_qdrant.repositories.nodes import QdrantNodeRepository

                repo = QdrantNodeRepository(self._qdrant_client, collection)
                hits = await repo.search_similar(
                    node.embedding,
                    limit=1,
                    score_threshold=self._concept_threshold,
                    node_type=node.node_type,
                )
                if hits:
                    return PublicGraphBridge._NodeMatchOutcome(local_node_id=hits[0].node_id, created=False)
            except Exception:
                logger.debug("node concept-match failed in %s", collection, exc_info=True)

        # Miss — create a new local node. The local node_uuid is derived
        # deterministically from the (node_type, concept) key — NOT from the
        # remote uuid, which may have come from an older row predating the
        # deterministic-key migration. Trusting the remote uuid would break
        # the unique index on ``write_nodes.node_uuid`` whenever the same
        # concept already exists locally under a different historical id.
        from kt_db.keys import key_to_uuid, make_node_key

        new_key = make_node_key(node.node_type, node.concept)
        new_uuid = key_to_uuid(new_key)
        local_fact_ids = [str(local_fact_id_by_remote[fid]) for fid in node.fact_ids if fid in local_fact_id_by_remote]
        # ``RETURNING node_uuid`` lets us distinguish a real insert from an
        # ON CONFLICT no-op (PG returns no rows in the latter case). The
        # bridge's nodes_created vs nodes_matched counts depend on this —
        # without RETURNING the bridge would over-report inserts whenever
        # the local key already existed (e.g. created by a parallel
        # ingest).
        stmt = (
            pg_insert(WriteNode)
            .values(
                key=new_key,
                node_uuid=new_uuid,
                concept=node.concept,
                node_type=node.node_type,
                definition=node.definition,
                fact_ids=local_fact_ids or None,
            )
            .on_conflict_do_nothing(index_elements=[WriteNode.key])
            .returning(WriteNode.node_uuid)
        )
        result = await session.execute(stmt)
        returned = result.scalar_one_or_none()
        if returned is None:
            # ON CONFLICT no-op: a row with this key already exists. Look
            # up its node_uuid so we can return the *local* identity (not
            # the remote one) and report this as a match, not a create.
            existing = await session.execute(select(WriteNode.node_uuid).where(WriteNode.key == new_key))
            existing_uuid = existing.scalar_one_or_none()
            local_uuid = existing_uuid if existing_uuid is not None else new_uuid
            return PublicGraphBridge._NodeMatchOutcome(local_node_id=local_uuid, created=False)

        # Real insert — push the embedding into the target collection so
        # future cache imports in this graph will dedup against it.
        if node.embedding is not None and self._qdrant_client is not None:
            try:
                from kt_qdrant.repositories.nodes import QdrantNodeRepository

                await QdrantNodeRepository(self._qdrant_client, collection).upsert(
                    node_id=new_uuid,
                    embedding=node.embedding,
                    node_type=node.node_type,
                    concept=node.concept,
                )
            except Exception:
                logger.debug("node upsert into %s failed", collection, exc_info=True)

        return PublicGraphBridge._NodeMatchOutcome(local_node_id=new_uuid, created=True)

    # ── Internal: Qdrant helpers ──────────────────────────────────────

    async def _fetch_qdrant_vectors(self, *, collection: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[float]]:
        """Best-effort vector fetch — empty dict on any failure."""
        if not ids or self._qdrant_client is None:
            return {}
        try:
            points = await self._qdrant_client.retrieve(
                collection_name=collection,
                ids=[str(i) for i in ids],
                with_vectors=True,
                with_payload=False,
            )
        except Exception:
            logger.debug("Qdrant retrieve failed for %s", collection, exc_info=True)
            return {}
        out: dict[uuid.UUID, list[float]] = {}
        for p in points:
            v = p.vector
            if isinstance(v, list):
                out[uuid.UUID(str(p.id))] = v
        return out

    async def _delete_qdrant_points(self, collection: str, ids: list[uuid.UUID]) -> None:
        if not ids or self._qdrant_client is None:
            return
        try:
            await self._qdrant_client.delete(
                collection_name=collection,
                points_selector=[str(i) for i in ids],
            )
        except Exception:
            logger.debug("Qdrant compensating delete failed for %s", collection, exc_info=True)

    # ── Internal: misc ────────────────────────────────────────────────

    def _is_stale(self, retrieved_at: datetime | None) -> bool:
        if retrieved_at is None or self._refresh_after.total_seconds() <= 0:
            return False
        # ``retrieved_at`` is stored naive UTC (see ``models._utcnow``);
        # strip the tzinfo from "now" so the subtraction stays naive.
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        return now_naive - retrieved_at > self._refresh_after


# ---------------------------------------------------------------------------
# Snapshot helpers (kept module-level so tests can build snapshots directly)
# ---------------------------------------------------------------------------


def _snapshot_raw_source(row: WriteRawSource) -> CachedRawSourceSnapshot:
    return CachedRawSourceSnapshot(
        id=row.id,
        uri=row.uri,
        canonical_url=row.canonical_url,
        doi=row.doi,
        title=row.title,
        raw_content=row.raw_content,
        content_hash=row.content_hash,
        content_type=row.content_type,
        provider_id=row.provider_id,
        is_full_text=row.is_full_text,
        is_super_source=row.is_super_source,
        fact_count=row.fact_count,
        retrieved_at=getattr(row, "updated_at", None),
    )


def _snapshot_fact(row: WriteFact, embedding: list[float] | None) -> CachedFactSnapshot:
    return CachedFactSnapshot(
        id=row.id,
        content=row.content,
        fact_type=row.fact_type,
        metadata_=row.metadata_,
        embedding=embedding,
    )


def _snapshot_fact_source(row: WriteFactSource) -> CachedFactSourceSnapshot:
    return CachedFactSourceSnapshot(
        fact_id=row.fact_id,
        raw_source_uri=row.raw_source_uri,
        raw_source_title=row.raw_source_title,
        raw_source_content_hash=row.raw_source_content_hash,
        raw_source_provider_id=row.raw_source_provider_id,
        context_snippet=row.context_snippet,
        attribution=row.attribution,
        author_person=row.author_person,
        author_org=row.author_org,
    )


def _snapshot_node(row: WriteNode, embedding: list[float] | None) -> CachedNodeSnapshot:
    fact_ids: list[uuid.UUID] = []
    for fid in row.fact_ids or []:
        try:
            fact_ids.append(uuid.UUID(fid))
        except (ValueError, TypeError):
            continue
    return CachedNodeSnapshot(
        key=row.key,
        node_uuid=row.node_uuid,
        concept=row.concept,
        node_type=row.node_type,
        definition=row.definition,
        embedding=embedding,
        fact_ids=fact_ids,
    )
