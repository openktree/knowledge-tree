"""Write-optimized node repository.

All operations target the write-db with deterministic TEXT keys.
No advisory locks, no FK validation — just fast upserts.
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import key_to_uuid, make_node_key
from kt_db.repositories.nodes import _exact_match_order
from kt_db.write_models import WriteNode, WriteNodeCounter


class WriteNodeRepository:
    """Upsert-only repository for the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def node_key(node_type: str, concept: str) -> str:
        return make_node_key(node_type, concept)

    async def upsert(
        self,
        node_type: str,
        concept: str,
        *,
        parent_key: str | None = None,
        source_concept_key: str | None = None,
        definition: str | None = None,
        definition_source: str | None = None,
        attractor: str | None = None,
        filter_id: str | None = None,
        max_content_tokens: int = 500,
        stale_after: int = 30,
        metadata_: dict | None = None,
        entity_subtype: str | None = None,
        enrichment_status: str | None = None,
    ) -> str:
        """Insert or update a node. Returns the deterministic key."""
        key = make_node_key(node_type, concept)
        node_uuid = key_to_uuid(key)

        update_set: dict[str, object] = {"updated_at": func.clock_timestamp()}
        values: dict[str, object] = {
            "key": key,
            "node_uuid": node_uuid,
            "concept": concept,
            "node_type": node_type,
            "parent_key": parent_key,
            "source_concept_key": source_concept_key,
            "definition": definition,
            "definition_source": definition_source,
            "attractor": attractor,
            "filter_id": filter_id,
            "max_content_tokens": max_content_tokens,
            "stale_after": stale_after,
            "metadata_": metadata_,
            "entity_subtype": entity_subtype,
            "enrichment_status": enrichment_status,
        }

        # Build update set: only non-None values (preserve existing data)
        for field in (
            "parent_key",
            "source_concept_key",
            "definition",
            "definition_source",
            "attractor",
            "filter_id",
            "entity_subtype",
            "enrichment_status",
        ):
            if values[field] is not None:
                update_set[field] = values[field]
        if metadata_ is not None:
            update_set["metadata"] = metadata_
        for field in ("max_content_tokens", "stale_after", "concept", "node_type"):
            update_set[field] = values[field]

        stmt = (
            pg_insert(WriteNode)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[WriteNode.key],
                set_=update_set,
            )
        )
        await self._session.execute(stmt)
        return key

    async def increment_access_count(self, node_key: str) -> None:
        stmt = (
            pg_insert(WriteNodeCounter)
            .values(node_key=node_key, access_count=1, update_count=0)
            .on_conflict_do_update(
                index_elements=[WriteNodeCounter.node_key],
                set_={"access_count": WriteNodeCounter.access_count + 1, "updated_at": func.clock_timestamp()},
            )
        )
        await self._session.execute(stmt)

    async def append_fact_id(self, node_key: str, fact_id: str) -> None:
        """Append a single fact ID to the node's fact_ids array."""
        stmt = text(
            "UPDATE write_nodes "
            "SET fact_ids = array_append(COALESCE(fact_ids, '{}'::text[]), :fid), "
            "    updated_at = NOW() "
            "WHERE key = :key"
        )
        await self._session.execute(stmt, {"key": node_key, "fid": fact_id})

    async def remove_fact_id(self, node_key: str, fact_id: str) -> None:
        """Remove a single fact ID from the node's fact_ids array."""
        stmt = text(
            "UPDATE write_nodes "
            "SET fact_ids = array_remove(COALESCE(fact_ids, '{}'::text[]), :fid), "
            "    updated_at = NOW() "
            "WHERE key = :key"
        )
        await self._session.execute(stmt, {"key": node_key, "fid": fact_id})

    async def get_by_uuids(
        self,
        node_ids: list[uuid.UUID],
    ) -> list[WriteNode]:
        """Look up WriteNodes by their deterministic UUIDs."""
        if not node_ids:
            return []
        stmt = select(WriteNode).where(WriteNode.node_uuid.in_(node_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_uuid(self, node_id: uuid.UUID) -> WriteNode | None:
        """Look up a single WriteNode by its deterministic UUID."""
        stmt = select(WriteNode).where(WriteNode.node_uuid == node_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_children_by_parent_key(self, parent_key: str) -> list[WriteNode]:
        """Return all WriteNodes whose parent_key matches."""
        stmt = select(WriteNode).where(WriteNode.parent_key == parent_key)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_key(self, key: str) -> WriteNode | None:
        """Look up a WriteNode by its TEXT key."""
        stmt = select(WriteNode).where(WriteNode.key == key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_metadata(self, key: str, metadata_: dict) -> None:
        """Update a node's metadata JSON field."""
        import json

        stmt = text(r"UPDATE write_nodes SET metadata = :meta\:\:jsonb, updated_at = NOW() WHERE key = :key")
        await self._session.execute(stmt, {"key": key, "meta": json.dumps(metadata_)})

    async def remove_metadata_key(self, node_key: str, meta_key: str) -> None:
        """Remove a single key from a node's metadata JSON without overwriting other keys."""
        stmt = text("UPDATE write_nodes SET metadata = metadata - :meta_key, updated_at = NOW() WHERE key = :node_key")
        await self._session.execute(stmt, {"node_key": node_key, "meta_key": meta_key})

    async def increment_update_count(self, node_key: str) -> None:
        stmt = (
            pg_insert(WriteNodeCounter)
            .values(node_key=node_key, access_count=0, update_count=1)
            .on_conflict_do_update(
                index_elements=[WriteNodeCounter.node_key],
                set_={"update_count": WriteNodeCounter.update_count + 1, "updated_at": func.clock_timestamp()},
            )
        )
        await self._session.execute(stmt)

    async def delete_by_key(self, node_key: str) -> bool:
        """Delete a node and its counters by key. Returns True if deleted."""
        from sqlalchemy import delete as sa_delete

        await self._session.execute(sa_delete(WriteNodeCounter).where(WriteNodeCounter.node_key == node_key))
        result = await self._session.execute(sa_delete(WriteNode).where(WriteNode.key == node_key))
        return (result.rowcount or 0) > 0

    async def merge_fact_ids(self, target_key: str, source_fact_ids: list[str]) -> None:
        """Merge fact IDs from a source into a target node, deduplicating."""
        if not source_fact_ids:
            return

        stmt = text(
            "UPDATE write_nodes "
            "SET fact_ids = ("
            "  SELECT array_agg(DISTINCT elem) "
            "  FROM unnest(COALESCE(fact_ids, '{}'::text[]) || :new_ids::text[]) AS elem"
            "), updated_at = NOW() "
            "WHERE key = :key"
        )
        await self._session.execute(stmt, {"key": target_key, "new_ids": source_fact_ids})

    async def update_facts_at_last_build(self, node_key: str, count: int) -> None:
        """Update the facts_at_last_build counter for a node."""
        stmt = text("UPDATE write_nodes SET facts_at_last_build = :count, updated_at = NOW() WHERE key = :key")
        await self._session.execute(stmt, {"key": node_key, "count": count})

    async def search_by_trigram(
        self,
        query: str,
        threshold: float = 0.3,
        limit: int = 5,
        node_type: str | None = None,
    ) -> list[WriteNode]:
        """Search write-db nodes by concept using pg_trgm similarity.

        Mirrors NodeRepository.search_by_trigram but targets write-db,
        avoiding graph-db pool pressure during pipeline fan-out.
        """
        stmt = (
            select(WriteNode)
            .where(func.similarity(WriteNode.concept, query) >= threshold)
            .order_by(
                _exact_match_order(WriteNode.concept, query),
                func.similarity(WriteNode.concept, query).desc(),
                func.length(WriteNode.concept).asc(),
            )
        )
        if node_type is not None:
            stmt = stmt.where(WriteNode.node_type == node_type)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
