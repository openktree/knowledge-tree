"""Write-optimized edge repository.

All operations target the write-db with deterministic TEXT keys.
No FK constraints, no deadlocks — just fast upserts.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import make_edge_key
from kt_db.write_models import WriteEdge, WriteNode


class WriteEdgeRepository:
    """Upsert-only repository for the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def edge_key(rel_type: str, node_key_a: str, node_key_b: str) -> str:
        return make_edge_key(rel_type, node_key_a, node_key_b)

    async def upsert(
        self,
        rel_type: str,
        source_node_key: str,
        target_node_key: str,
        weight: float = 0.5,
        *,
        justification: str | None = None,
        fact_ids: list[str] | None = None,
        metadata_: dict | None = None,
        weight_source: str | None = None,
    ) -> str:
        """Insert or update an edge. Returns the deterministic key."""
        from kt_config.types import UNDIRECTED_EDGE_TYPES

        key = make_edge_key(rel_type, source_node_key, target_node_key)
        # Canonical ordering for undirected edges; preserve order for directed
        if rel_type in UNDIRECTED_EDGE_TYPES:
            a, b = sorted([source_node_key, target_node_key])
        else:
            a, b = source_node_key, target_node_key

        update_set: dict[str, object] = {
            "weight": weight,
            "updated_at": func.clock_timestamp(),
        }
        if justification is not None:
            update_set["justification"] = justification
        if fact_ids is not None:
            update_set["fact_ids"] = fact_ids
        if metadata_ is not None:
            update_set["metadata"] = metadata_

        stmt = (
            pg_insert(WriteEdge)
            .values(
                key=key,
                source_node_key=a,
                target_node_key=b,
                relationship_type=rel_type,
                weight=weight,
                justification=justification,
                weight_source=weight_source,
                fact_ids=fact_ids,
                metadata_=metadata_,
            )
            .on_conflict_do_update(
                index_elements=[WriteEdge.key],
                set_=update_set,
            )
        )
        await self._session.execute(stmt)
        return key

    async def get_recent_edge_pairs(
        self,
        node_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
        staleness_days: int = 30,
    ) -> set[uuid.UUID]:
        """Return candidate IDs that already have a recent edge with *node_id*.

        Resolves UUIDs to TEXT keys via ``key_to_uuid``, then checks
        ``write_edges`` for matching source/target pairs.
        """
        if not candidate_ids:
            return set()

        # Build UUID→key mapping for the relevant nodes
        all_ids = list({node_id} | set(candidate_ids))
        node_result = await self._session.execute(select(WriteNode).where(WriteNode.node_uuid.in_(all_ids)))
        uuid_to_key: dict[uuid.UUID, str] = {}
        for wn in node_result.scalars().all():
            uuid_to_key[wn.node_uuid] = wn.key

        source_key = uuid_to_key.get(node_id)
        if source_key is None:
            return set()

        # Load recent edges involving the source node
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=staleness_days)
        edge_result = await self._session.execute(
            select(WriteEdge).where(
                WriteEdge.created_at >= cutoff,
                (WriteEdge.source_node_key == source_key) | (WriteEdge.target_node_key == source_key),
            )
        )

        stale: set[uuid.UUID] = set()
        key_to_uuid_map = {v: k for k, v in uuid_to_key.items()}
        for edge in edge_result.scalars().all():
            other_key = edge.target_node_key if edge.source_node_key == source_key else edge.source_node_key
            other_uuid = key_to_uuid_map.get(other_key)
            if other_uuid is not None and other_uuid in set(candidate_ids):
                stale.add(other_uuid)

        return stale

    async def append_fact_id(self, edge_key: str, fact_id: str) -> None:
        """Append a single fact ID to the edge's fact_ids array."""
        stmt = text(
            "UPDATE write_edges "
            "SET fact_ids = array_append(COALESCE(fact_ids, '{}'::text[]), :fid), "
            "    updated_at = NOW() "
            "WHERE key = :key"
        )
        await self._session.execute(stmt, {"key": edge_key, "fid": fact_id})

    async def get_edges_for_node(self, node_key: str) -> list[WriteEdge]:
        """Return all edges involving a given node key."""
        stmt = select(WriteEdge).where(
            (WriteEdge.source_node_key == node_key) | (WriteEdge.target_node_key == node_key)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_key(self, edge_key: str) -> bool:
        """Delete an edge by its primary key. Returns True if deleted."""
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(WriteEdge).where(WriteEdge.key == edge_key)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def delete(self, rel_type: str, node_key_a: str, node_key_b: str) -> bool:
        """Delete an edge by its deterministic key. Returns True if deleted."""
        from sqlalchemy import delete as sa_delete

        key = make_edge_key(rel_type, node_key_a, node_key_b)
        stmt = sa_delete(WriteEdge).where(WriteEdge.key == key)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
