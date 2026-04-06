"""Write-optimized seed repository.

All operations target the write-db. Seeds are lightweight proto-nodes
that track entity/concept mentions during fact decomposition.
"""

import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import key_to_uuid
from kt_db.write_models import (
    WriteEdgeCandidate,
    WriteSeed,
    WriteSeedFact,
    WriteSeedMerge,
    WriteSeedRoute,
)

logger = logging.getLogger(__name__)


class WriteSeedRepository:
    """Upsert-friendly repository for seeds in the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Seed CRUD ──────────────────────────────────────────────────────

    async def upsert_seed(
        self,
        key: str,
        name: str,
        node_type: str,
        entity_subtype: str | None = None,
    ) -> WriteSeed:
        """Insert or update a seed. Does NOT touch fact_count — that is
        maintained by ``refresh_fact_counts`` after linking facts."""
        seed_uuid = key_to_uuid(key)
        stmt = (
            pg_insert(WriteSeed)
            .values(
                key=key,
                seed_uuid=seed_uuid,
                name=name,
                node_type=node_type,
                entity_subtype=entity_subtype,
                fact_count=0,
            )
            .on_conflict_do_update(
                index_elements=[WriteSeed.key],
                set_={
                    "updated_at": func.now(),
                },
            )
        )
        await self._session.execute(stmt)
        # Fetch and return the seed
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.key == key))
        return result.scalar_one()

    async def get_seed_by_key(self, key: str) -> WriteSeed | None:
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.key == key))
        return result.scalar_one_or_none()

    async def get_seeds_by_status(self, status: str, limit: int = 100) -> list[WriteSeed]:
        result = await self._session.execute(
            select(WriteSeed).where(WriteSeed.status == status).order_by(WriteSeed.fact_count.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_seeds_by_keys(self, keys: list[str]) -> list[WriteSeed]:
        if not keys:
            return []
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.key.in_(keys)))
        return list(result.scalars().all())

    async def get_seeds_by_keys_batch(
        self,
        seed_keys: list[str],
    ) -> dict[str, WriteSeed]:
        """Batch fetch seeds by keys. Returns key → WriteSeed mapping."""
        if not seed_keys:
            return {}
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.key.in_(seed_keys)))
        return {s.key: s for s in result.scalars().all()}

    async def update_seed_metadata(self, seed_key: str, metadata: dict) -> None:
        """Merge metadata into a seed's existing metadata_ JSONB field."""
        seed = await self.get_seed_by_key(seed_key)
        if not seed:
            return
        merged = seed.metadata_ or {}
        merged.update(metadata)
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == seed_key).values(metadata_=merged, updated_at=func.now())
        )

    async def list_perspective_pairs(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        source_node_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[WriteSeed], int]:
        """List thesis perspective seeds (with antithesis in metadata).

        Returns (seeds, total_count) for pagination.
        """
        from sqlalchemy import text

        base = select(WriteSeed).where(
            WriteSeed.node_type == "perspective",
            WriteSeed.metadata_["dialectic_role"].astext == "thesis",
        )
        count_base = (
            select(func.count())
            .select_from(WriteSeed)
            .where(
                WriteSeed.node_type == "perspective",
                WriteSeed.metadata_["dialectic_role"].astext == "thesis",
            )
        )

        if status:
            base = base.where(WriteSeed.status == status)
            count_base = count_base.where(WriteSeed.status == status)
        else:
            # Default: exclude dismissed and garbage seeds
            base = base.where(WriteSeed.status.notin_(["dismissed", "garbage"]))
            count_base = count_base.where(WriteSeed.status.notin_(["dismissed", "garbage"]))

        if search:
            base = base.where(WriteSeed.name.ilike(f"%{search}%"))
            count_base = count_base.where(WriteSeed.name.ilike(f"%{search}%"))

        if source_node_id:
            # Filter by source_node_id in metadata->source_node_ids JSON array
            json_filter = text("metadata->>'source_node_ids' LIKE :pattern").bindparams(pattern=f"%{source_node_id}%")
            base = base.where(json_filter)
            count_base = count_base.where(json_filter)

        base = base.order_by(WriteSeed.fact_count.desc()).offset(offset).limit(limit)

        result = await self._session.execute(base)
        seeds = list(result.scalars().all())

        count_result = await self._session.execute(count_base)
        total = count_result.scalar() or 0

        return seeds, total

    # ── Seed-Fact linking ──────────────────────────────────────────────

    async def refresh_fact_counts(self, seed_keys: list[str]) -> None:
        """Set fact_count to the actual number of WriteSeedFact rows for each seed."""
        if not seed_keys:
            return
        # Subquery: count facts per seed, excluding source_attribution links
        counts_subq = (
            select(
                WriteSeedFact.seed_key,
                func.count().label("cnt"),
            )
            .where(
                WriteSeedFact.seed_key.in_(seed_keys),
                WriteSeedFact.extraction_role != "source_attribution",
            )
            .group_by(WriteSeedFact.seed_key)
        ).subquery()

        # Zero out seeds that may only have source_attribution links
        await self._session.execute(update(WriteSeed).where(WriteSeed.key.in_(seed_keys)).values(fact_count=0))

        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == counts_subq.c.seed_key).values(fact_count=counts_subq.c.cnt)
        )

    async def link_fact(
        self,
        seed_key: str,
        fact_id: uuid.UUID,
        confidence: float = 1.0,
        extraction_context: str | None = None,
        extraction_role: str = "mentioned",
    ) -> bool:
        """Link a fact to a seed. Returns True if new link created."""
        stmt = (
            pg_insert(WriteSeedFact)
            .values(
                id=uuid.uuid4(),
                seed_key=seed_key,
                fact_id=fact_id,
                confidence=confidence,
                extraction_context=extraction_context,
                extraction_role=extraction_role,
            )
            .on_conflict_do_nothing(index_elements=["seed_key", "fact_id"])
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[return-value]

    async def get_mentioned_facts_for_seed(self, seed_key: str) -> list[uuid.UUID]:
        """Get fact IDs for a seed, excluding source_attribution links."""
        result = await self._session.execute(
            select(WriteSeedFact.fact_id).where(
                WriteSeedFact.seed_key == seed_key,
                WriteSeedFact.extraction_role != "source_attribution",
            )
        )
        return [row[0] for row in result.all()]

    async def get_facts_for_seed(self, seed_key: str) -> list[uuid.UUID]:
        result = await self._session.execute(select(WriteSeedFact.fact_id).where(WriteSeedFact.seed_key == seed_key))
        return [row[0] for row in result.all()]

    async def get_seed_facts(self, seed_key: str) -> list[WriteSeedFact]:
        result = await self._session.execute(select(WriteSeedFact).where(WriteSeedFact.seed_key == seed_key))
        return list(result.scalars().all())

    # ── Edge candidates ────────────────────────────────────────────────

    async def upsert_edge_candidate(
        self,
        seed_key_a: str,
        seed_key_b: str,
        fact_id: uuid.UUID,
        discovery_strategy: str = "seed_cooccurrence",
    ) -> None:
        """Insert one edge candidate fact row. No-op on conflict.

        Keys must be pre-sorted (alphabetical canonical order).
        """
        stmt = (
            pg_insert(WriteEdgeCandidate)
            .values(
                id=uuid.uuid4(),
                seed_key_a=seed_key_a,
                seed_key_b=seed_key_b,
                fact_id=str(fact_id),
                discovery_strategy=discovery_strategy,
            )
            .on_conflict_do_nothing(constraint="uq_wec_pair_fact")
        )
        await self._session.execute(stmt)

    async def get_edge_candidates(
        self,
        status: str = "pending",
        min_fact_count: int = 1,
    ) -> list[tuple[str, str, int]]:
        """Get seed pairs with at least min_fact_count facts of given status.

        Returns (seed_key_a, seed_key_b, count) tuples.
        """
        from sqlalchemy import func as sa_func

        result = await self._session.execute(
            select(
                WriteEdgeCandidate.seed_key_a,
                WriteEdgeCandidate.seed_key_b,
                sa_func.count().label("cnt"),
            )
            .where(WriteEdgeCandidate.status == status)
            .group_by(WriteEdgeCandidate.seed_key_a, WriteEdgeCandidate.seed_key_b)
            .having(sa_func.count() >= min_fact_count)
            .order_by(sa_func.count().desc())
        )
        return [(r[0], r[1], r[2]) for r in result.all()]

    async def get_candidates_for_seed(
        self,
        seed_key: str,
        status: str = "pending",
    ) -> list[WriteEdgeCandidate]:
        """Get all candidate fact rows for a seed filtered by status."""
        result = await self._session.execute(
            select(WriteEdgeCandidate).where(
                ((WriteEdgeCandidate.seed_key_a == seed_key) | (WriteEdgeCandidate.seed_key_b == seed_key)),
                WriteEdgeCandidate.status == status,
            )
        )
        return list(result.scalars().all())

    async def reject_candidate_facts(
        self,
        seed_key_a: str,
        seed_key_b: str,
        fact_ids: list[str],
        evaluation_result: dict | None = None,
    ) -> None:
        """Mark specific candidate fact rows as rejected."""
        a, b = sorted([seed_key_a, seed_key_b])
        await self._session.execute(
            update(WriteEdgeCandidate)
            .where(
                WriteEdgeCandidate.seed_key_a == a,
                WriteEdgeCandidate.seed_key_b == b,
                WriteEdgeCandidate.fact_id.in_(fact_ids),
            )
            .values(
                status="rejected",
                evaluation_result=evaluation_result,
                last_evaluated_at=func.now(),
            )
        )

    async def accept_candidate_facts(
        self,
        seed_key_a: str,
        seed_key_b: str,
        fact_ids: list[str],
    ) -> None:
        """Mark specific candidate fact rows as accepted."""
        a, b = sorted([seed_key_a, seed_key_b])
        await self._session.execute(
            update(WriteEdgeCandidate)
            .where(
                WriteEdgeCandidate.seed_key_a == a,
                WriteEdgeCandidate.seed_key_b == b,
                WriteEdgeCandidate.fact_id.in_(fact_ids),
            )
            .values(status="accepted", last_evaluated_at=func.now())
        )

    # ── Routes (disambiguation pipes) ───────────────────────────────────

    async def create_route(
        self,
        parent_key: str,
        child_key: str,
        label: str,
        ambiguity_type: str = "text",
    ) -> WriteSeedRoute:
        """Create a route from an ambiguous parent seed to a disambiguated child."""
        stmt = (
            pg_insert(WriteSeedRoute)
            .values(
                id=uuid.uuid4(),
                parent_seed_key=parent_key,
                child_seed_key=child_key,
                label=label,
                ambiguity_type=ambiguity_type,
            )
            .on_conflict_do_nothing(constraint="uq_wsr_parent_child")
        )
        await self._session.execute(stmt)
        result = await self._session.execute(
            select(WriteSeedRoute).where(
                WriteSeedRoute.parent_seed_key == parent_key,
                WriteSeedRoute.child_seed_key == child_key,
            )
        )
        return result.scalar_one()

    async def rename_seed(self, seed_key: str, new_name: str) -> None:
        """Rename seed to a more specific name, keeping old name as alias."""
        seed = await self.get_seed_by_key(seed_key)
        if seed is None:
            return
        old_name = seed.name
        if old_name.lower().strip() == new_name.lower().strip():
            return
        # Keep old name as alias
        meta = seed.metadata_ or {}
        aliases = meta.get("aliases", [])
        if old_name not in aliases:
            aliases.append(old_name)
        meta["aliases"] = aliases
        await self._session.execute(
            update(WriteSeed)
            .where(WriteSeed.key == seed_key)
            .values(name=new_name, metadata_=meta, updated_at=func.now())
        )

    async def get_routes_for_parent(self, parent_key: str) -> list[WriteSeedRoute]:
        """Get all child routes for an ambiguous parent seed."""
        result = await self._session.execute(select(WriteSeedRoute).where(WriteSeedRoute.parent_seed_key == parent_key))
        return list(result.scalars().all())

    async def get_route_for_child(self, child_key: str) -> WriteSeedRoute | None:
        """Reverse lookup: find a route pointing to this child (first match)."""
        result = await self._session.execute(
            select(WriteSeedRoute).where(WriteSeedRoute.child_seed_key == child_key).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_routes_for_children_batch(self, child_keys: list[str]) -> dict[str, WriteSeedRoute]:
        """Batch fetch routes where these keys are children. Returns child_key -> route mapping."""
        if not child_keys:
            return {}
        result = await self._session.execute(
            select(WriteSeedRoute).where(WriteSeedRoute.child_seed_key.in_(child_keys))
        )
        return {r.child_seed_key: r for r in result.scalars().all()}

    # ── Phonetic matching ─────────────────────────────────────────────

    async def find_by_phonetic(
        self,
        phonetic_code: str,
        node_type: str,
        limit: int = 10,
    ) -> list[WriteSeed]:
        """Find seeds with matching phonetic code."""
        if not phonetic_code:
            return []
        result = await self._session.execute(
            select(WriteSeed)
            .where(
                WriteSeed.phonetic_code == phonetic_code,
                WriteSeed.node_type == node_type,
                WriteSeed.status.in_(["active", "promoted", "ambiguous"]),
            )
            .order_by(WriteSeed.fact_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_phonetic_code(self, seed_key: str, code: str) -> None:
        """Update the phonetic code for a seed."""
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == seed_key).values(phonetic_code=code, updated_at=func.now())
        )

    async def update_context_hash(self, seed_key: str, hash_value: str) -> None:
        """Update the context hash for a seed (staleness detection)."""
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == seed_key).values(context_hash=hash_value, updated_at=func.now())
        )

    # ── Merge / Split ──────────────────────────────────────────────────

    async def merge_seeds(
        self,
        losing_key: str,
        winning_key: str,
        reason: str | None = None,
    ) -> WriteSeedMerge:
        """Merge losing seed into winning seed.

        Reassigns facts and edge candidates, records audit trail.
        The losing seed is marked as 'merged', never deleted.
        """
        # Get facts from losing seed
        losing_facts = await self.get_facts_for_seed(losing_key)
        fact_id_strs = [str(fid) for fid in losing_facts]

        # Reassign seed-fact links
        await self._session.execute(
            update(WriteSeedFact)
            .where(WriteSeedFact.seed_key == losing_key)
            .values(seed_key=winning_key, updated_at=func.now())
        )

        # Reassign edge candidates (update references to losing key).
        # Delete duplicates first to avoid unique constraint violations
        # when both losing and winning seeds share candidates for the
        # same partner seed + fact.
        from sqlalchemy import and_, delete, exists
        from sqlalchemy import select as sa_select

        # Remove losing-key rows that would collide with existing winning-key rows
        # (same partner seed + fact already exists under winning key)
        for col_to_update, other_col in [
            ("seed_key_a", "seed_key_b"),
            ("seed_key_b", "seed_key_a"),
        ]:
            losing_col = getattr(WriteEdgeCandidate, col_to_update)
            other_col_attr = getattr(WriteEdgeCandidate, other_col)

            # Alias for the subquery
            from sqlalchemy.orm import aliased

            WEC2 = aliased(WriteEdgeCandidate)
            wec2_update_col = getattr(WEC2, col_to_update)
            wec2_other_col = getattr(WEC2, other_col)

            # Delete rows from losing key that would conflict after reassignment
            conflict_subq = (
                sa_select(WEC2.id)
                .where(
                    wec2_update_col == winning_key,
                    wec2_other_col == other_col_attr,
                    WEC2.fact_id == WriteEdgeCandidate.fact_id,
                )
                .correlate(WriteEdgeCandidate)
            )

            await self._session.execute(
                delete(WriteEdgeCandidate).where(
                    losing_col == losing_key,
                    exists(conflict_subq),
                )
            )

            # Now safely reassign remaining rows
            await self._session.execute(
                update(WriteEdgeCandidate)
                .where(losing_col == losing_key)
                .values(**{col_to_update: winning_key}, updated_at=func.now())
            )

        # Also handle self-referential candidates (losing_key with winning_key)
        # that would become (winning_key, winning_key) — delete those
        await self._session.execute(
            delete(WriteEdgeCandidate).where(
                and_(
                    WriteEdgeCandidate.seed_key_a == winning_key,
                    WriteEdgeCandidate.seed_key_b == winning_key,
                )
            )
        )

        # Update losing seed status
        await self._session.execute(
            update(WriteSeed)
            .where(WriteSeed.key == losing_key)
            .values(
                status="merged",
                merged_into_key=winning_key,
                updated_at=func.now(),
            )
        )

        # Update winning seed fact_count (excluding source_attribution links)
        new_count_result = await self._session.execute(
            select(func.count())
            .select_from(WriteSeedFact)
            .where(
                WriteSeedFact.seed_key == winning_key,
                WriteSeedFact.extraction_role != "source_attribution",
            )
        )
        new_count = new_count_result.scalar() or 0
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == winning_key).values(fact_count=new_count, updated_at=func.now())
        )

        # Append losing seed's name to winning seed's aliases
        winning_seed = await self.get_seed_by_key(winning_key)
        losing_seed = await self.get_seed_by_key(losing_key)
        if winning_seed and losing_seed:
            meta = winning_seed.metadata_ or {}
            aliases = meta.get("aliases", [])
            if losing_seed.name not in aliases:
                aliases.append(losing_seed.name)
            meta["aliases"] = aliases
            await self._session.execute(update(WriteSeed).where(WriteSeed.key == winning_key).values(metadata_=meta))

        # Record audit
        merge_record = WriteSeedMerge(
            id=uuid.uuid4(),
            operation="merge",
            source_seed_key=losing_key,
            target_seed_key=winning_key,
            reason=reason,
            fact_ids_moved=fact_id_strs,
        )
        self._session.add(merge_record)
        await self._session.flush()
        return merge_record

    async def split_seed(
        self,
        original_key: str,
        new_seeds: list[dict],
        fact_assignments: dict[str, list[uuid.UUID]],
        reason: str | None = None,
    ) -> list[WriteSeedMerge]:
        """Split a seed into multiple new seeds.

        Args:
            original_key: The seed being split
            new_seeds: List of dicts with keys: key, name, node_type, entity_subtype,
                and optionally ``label`` for the disambiguation route.
            fact_assignments: Map of new_seed_key -> list of fact_ids
            reason: Reason for the split
        """
        records = []
        for seed_data in new_seeds:
            new_key = seed_data["key"]
            seed_uuid = key_to_uuid(new_key)
            assigned_facts = fact_assignments.get(new_key, [])

            # Create new seed
            stmt = (
                pg_insert(WriteSeed)
                .values(
                    key=new_key,
                    seed_uuid=seed_uuid,
                    name=seed_data["name"],
                    node_type=seed_data["node_type"],
                    entity_subtype=seed_data.get("entity_subtype"),
                    fact_count=len(assigned_facts),
                )
                .on_conflict_do_nothing()
            )
            await self._session.execute(stmt)

            # Copy seed-fact links from original, preserving extraction_role
            if assigned_facts:
                existing_links = await self._session.execute(
                    select(WriteSeedFact).where(
                        WriteSeedFact.seed_key == original_key,
                        WriteSeedFact.fact_id.in_(assigned_facts),
                    )
                )
                linked_fact_ids: set[uuid.UUID] = set()
                for link in existing_links.scalars():
                    await self.link_fact(
                        new_key,
                        link.fact_id,
                        confidence=link.confidence,
                        extraction_context=link.extraction_context,
                        extraction_role=link.extraction_role,
                    )
                    linked_fact_ids.add(link.fact_id)
                # Fallback: link any facts not found on original (shouldn't happen)
                for fact_id in assigned_facts:
                    if fact_id not in linked_fact_ids:
                        await self.link_fact(new_key, fact_id)

            # Create route from parent to child
            label = seed_data.get("label", seed_data["name"])
            await self.create_route(original_key, new_key, label)

            # Record audit
            merge_record = WriteSeedMerge(
                id=uuid.uuid4(),
                operation="split",
                source_seed_key=original_key,
                target_seed_key=new_key,
                reason=reason,
                fact_ids_moved=[str(fid) for fid in assigned_facts],
            )
            self._session.add(merge_record)
            records.append(merge_record)

        # Mark original as ambiguous
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == original_key).values(status="ambiguous", updated_at=func.now())
        )

        await self._session.flush()
        return records

    # ── Promotion ──────────────────────────────────────────────────────

    async def promote_seed(self, seed_key: str, node_key: str) -> bool:
        """Mark a seed as promoted to a full node. Returns True if updated."""
        result = await self._session.execute(
            update(WriteSeed)
            .where(WriteSeed.key == seed_key, WriteSeed.status.in_(["active", "ambiguous"]))
            .values(
                status="promoted",
                promoted_node_key=node_key,
                updated_at=func.now(),
            )
        )
        return result.rowcount > 0  # type: ignore[return-value]

    # ── Garbage ────────────────────────────────────────────────────────

    async def mark_as_garbage(self, seed_key: str, reason: str | None = None) -> bool:
        """Mark a seed as garbage. Returns True if updated.

        Garbage seeds are unpromotable and hidden by default. They act as
        attractors during dedup — similar incoming seeds merge into them,
        keeping junk quarantined.
        """
        result = await self._session.execute(
            update(WriteSeed)
            .where(
                WriteSeed.key == seed_key,
                WriteSeed.status.in_(["active", "ambiguous"]),
            )
            .values(status="garbage", updated_at=func.now())
        )
        if result.rowcount and reason:  # type: ignore[truthy-bool]
            # Record reason in merge audit for traceability
            merge_record = WriteSeedMerge(
                id=__import__("uuid").uuid4(),
                operation="garbage",
                source_seed_key=seed_key,
                target_seed_key=seed_key,
                reason=reason,
                fact_ids_moved=[],
            )
            self._session.add(merge_record)
            await self._session.flush()
        return result.rowcount > 0  # type: ignore[return-value]

    # ── Alias lookup ─────────────────────────────────────────────────

    async def find_seeds_by_alias(
        self,
        alias: str,
        node_type: str,
        limit: int = 10,
    ) -> list[WriteSeed]:
        """Find active seeds whose metadata aliases contain the given string.

        Uses JSONB containment to search the ``aliases`` array within
        ``metadata_``.  Case-insensitive via lower() comparison.
        """
        if not alias:
            return []
        # JSONB containment: metadata @> '{"aliases": ["FBI"]}'
        # We need case-insensitive, so we use a raw SQL approach with jsonb_array_elements
        from sqlalchemy import text

        stmt = text("""
            SELECT ws.*
            FROM write_seeds ws,
                 jsonb_array_elements_text(COALESCE(ws.metadata->'aliases', '[]'::jsonb)) AS elem
            WHERE ws.node_type = :node_type
              AND ws.status IN ('active', 'promoted')
              AND lower(elem) = lower(:alias)
            ORDER BY ws.fact_count DESC
            LIMIT :limit
        """)
        result = await self._session.execute(stmt, {"node_type": node_type, "alias": alias, "limit": limit})
        # Map raw rows back to WriteSeed ORM objects
        rows = result.fetchall()
        if not rows:
            return []
        keys = [r.key for r in rows]
        orm_result = await self._session.execute(select(WriteSeed).where(WriteSeed.key.in_(keys)))
        return list(orm_result.scalars().all())

    async def update_aliases_batch(
        self,
        updates: list[tuple[str, list[str]]],
    ) -> None:
        """Merge new aliases into existing seed metadata.

        Each tuple is (seed_key, new_aliases). Existing aliases are preserved;
        new ones are appended (deduplicated, case-insensitive).
        """
        if not updates:
            return
        for seed_key, new_aliases in updates:
            if not new_aliases:
                continue
            seed = await self.get_seed_by_key(seed_key)
            if not seed:
                continue
            meta = seed.metadata_ or {}
            existing = meta.get("aliases", [])
            existing_lower = {a.lower() for a in existing}
            for alias in new_aliases:
                if alias.lower() not in existing_lower and alias.lower() != seed.name.lower():
                    existing.append(alias)
                    existing_lower.add(alias.lower())
            meta["aliases"] = existing
            await self._session.execute(
                update(WriteSeed).where(WriteSeed.key == seed_key).values(metadata_=meta, updated_at=func.now())
            )

    # ── Tree / descendant queries ───────────────────────────────────────

    async def get_all_descendant_facts(
        self,
        seed_key: str,
        max_depth: int = 10,
    ) -> list[uuid.UUID]:
        """Recursively collect fact IDs from all descendant seeds via BFS."""
        all_facts: set[uuid.UUID] = set()
        queue = [seed_key]
        visited: set[str] = set()
        depth = 0

        while queue and depth < max_depth:
            next_queue: list[str] = []
            for key in queue:
                if key in visited:
                    continue
                visited.add(key)
                facts = await self.get_facts_for_seed(key)
                all_facts.update(facts)
                routes = await self.get_routes_for_parent(key)
                for r in routes:
                    if r.child_seed_key not in visited:
                        next_queue.append(r.child_seed_key)
            queue = next_queue
            depth += 1

        return list(all_facts)

    async def get_seed_tree(
        self,
        seed_key: str,
        max_depth: int = 10,
    ) -> dict | None:
        """Build tree: walk UP to root ancestor, then DOWN to all leaves."""
        root_key = seed_key
        for _ in range(max_depth):
            route = await self.get_route_for_child(root_key)
            if not route:
                break
            root_key = route.parent_seed_key

        return await self._build_tree_node(root_key, max_depth, set())

    async def _build_tree_node(
        self,
        key: str,
        remaining_depth: int,
        visited: set[str],
    ) -> dict | None:
        if key in visited or remaining_depth <= 0:
            return None
        visited.add(key)
        seed = await self.get_seed_by_key(key)
        if not seed:
            return None
        routes = await self.get_routes_for_parent(key)
        route_to_self = await self.get_route_for_child(key)
        children = []
        for r in routes:
            child = await self._build_tree_node(
                r.child_seed_key,
                remaining_depth - 1,
                visited,
            )
            if child:
                child["ambiguity_type"] = r.ambiguity_type
                children.append(child)
        return {
            "key": seed.key,
            "name": seed.name,
            "status": seed.status,
            "node_type": seed.node_type,
            "fact_count": seed.fact_count,
            "promoted_node_key": seed.promoted_node_key,
            "ambiguity_type": getattr(route_to_self, "ambiguity_type", None) if route_to_self else None,
            "children": children,
        }

    # ── Similarity search ──────────────────────────────────────────────

    async def list_seeds(
        self,
        *,
        status: str | None = None,
        node_type: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 20,
        exclude_merged: bool = True,
        min_fact_count: int | None = None,
        promotable_only: bool = False,
    ) -> list[WriteSeed]:
        """List seeds with optional filters and pagination.

        By default excludes merged and garbage seeds since they are
        subsumed/quarantined. Pass an explicit status='merged' or
        status='garbage' to view them.

        When ``promotable_only`` is True, only active/ambiguous seeds are
        returned (regardless of ``status``).
        """
        stmt = select(WriteSeed)
        if promotable_only:
            stmt = stmt.where(WriteSeed.status.in_(["active", "ambiguous"]))
        elif status:
            stmt = stmt.where(WriteSeed.status == status)
        elif exclude_merged:
            stmt = stmt.where(WriteSeed.status.notin_(["merged", "garbage"]))
        if node_type:
            stmt = stmt.where(WriteSeed.node_type == node_type)
        if search:
            stmt = stmt.where(WriteSeed.name.ilike(f"%{search}%"))
        if min_fact_count is not None:
            stmt = stmt.where(WriteSeed.fact_count >= min_fact_count)
        stmt = stmt.order_by(WriteSeed.fact_count.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_seeds(
        self,
        *,
        status: str | None = None,
        node_type: str | None = None,
        search: str | None = None,
        exclude_merged: bool = True,
        min_fact_count: int | None = None,
        promotable_only: bool = False,
    ) -> int:
        """Count seeds with optional filters."""
        stmt = select(func.count()).select_from(WriteSeed)
        if promotable_only:
            stmt = stmt.where(WriteSeed.status.in_(["active", "ambiguous"]))
        elif status:
            stmt = stmt.where(WriteSeed.status == status)
        elif exclude_merged:
            stmt = stmt.where(WriteSeed.status.notin_(["merged", "garbage"]))
        if node_type:
            stmt = stmt.where(WriteSeed.node_type == node_type)
        if search:
            stmt = stmt.where(WriteSeed.name.ilike(f"%{search}%"))
        if min_fact_count is not None:
            stmt = stmt.where(WriteSeed.fact_count >= min_fact_count)
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_merges_for_seed(self, seed_key: str) -> list[WriteSeedMerge]:
        """Get all merge/split audit records involving a seed."""
        result = await self._session.execute(
            select(WriteSeedMerge)
            .where((WriteSeedMerge.source_seed_key == seed_key) | (WriteSeedMerge.target_seed_key == seed_key))
            .order_by(WriteSeedMerge.created_at.desc())
        )
        return list(result.scalars().all())

    # ── Batch operations ────────────────────────────────────────────

    async def upsert_seeds_batch(
        self,
        seeds: list[dict],
    ) -> None:
        """Batch upsert seeds. Each dict: {key, name, node_type, entity_subtype}.

        Does NOT touch fact_count — call ``refresh_fact_counts`` after linking facts.
        """
        if not seeds:
            return
        # Deduplicate by key — PostgreSQL ON CONFLICT DO UPDATE cannot
        # affect the same row twice in a single statement.
        seeds_deduped = list({s["key"]: s for s in seeds}.values())
        rows = [
            {
                "key": s["key"],
                "seed_uuid": key_to_uuid(s["key"]),
                "name": s["name"],
                "node_type": s["node_type"],
                "entity_subtype": s.get("entity_subtype"),
                "fact_count": 0,
            }
            for s in seeds_deduped
        ]
        chunk_size = 4000
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = (
                pg_insert(WriteSeed)
                .values(chunk)
                .on_conflict_do_update(
                    index_elements=[WriteSeed.key],
                    set_={
                        "updated_at": func.now(),
                    },
                )
            )
            await self._session.execute(stmt)

    async def link_facts_batch(
        self,
        links: list[dict],
    ) -> int:
        """Batch link facts to seeds.

        Each dict: {seed_key, fact_id, extraction_context, extraction_role}.
        Returns count of new links created.
        """
        if not links:
            return 0
        rows = [
            {
                "id": uuid.uuid4(),
                "seed_key": lnk["seed_key"],
                "fact_id": lnk["fact_id"],
                "confidence": lnk.get("confidence", 1.0),
                "extraction_context": lnk.get("extraction_context"),
                "extraction_role": lnk.get("extraction_role", "mentioned"),
            }
            for lnk in links
        ]
        chunk_size = 4000
        total_created = 0
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = pg_insert(WriteSeedFact).values(chunk).on_conflict_do_nothing(index_elements=["seed_key", "fact_id"])
            result = await self._session.execute(stmt)
            total_created += result.rowcount or 0
        return total_created  # type: ignore[return-value]

    async def upsert_edge_candidates_batch(
        self,
        candidates: list[dict],
    ) -> None:
        """Batch upsert edge candidates. Each dict: {seed_key_a, seed_key_b, fact_id}.

        Keys must be pre-sorted (alphabetical canonical order).
        """
        if not candidates:
            return
        rows = [
            {
                "id": uuid.uuid4(),
                "seed_key_a": c["seed_key_a"],
                "seed_key_b": c["seed_key_b"],
                "fact_id": c["fact_id"],
                "discovery_strategy": c.get("discovery_strategy", "seed_cooccurrence"),
            }
            for c in candidates
        ]
        # PostgreSQL has a 32767 query argument limit.
        # Each row has 7 columns (including defaults), so chunk at 4000 rows.
        chunk_size = 4000
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = pg_insert(WriteEdgeCandidate).values(chunk).on_conflict_do_nothing(constraint="uq_wec_pair_fact")
            await self._session.execute(stmt)

    async def list_edge_candidate_pairs(
        self,
        *,
        status_filter: str | None = None,
        search: str | None = None,
        min_facts: int = 1,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """List edge candidate pairs grouped by (seed_key_a, seed_key_b).

        Returns dicts with seed_key_a, seed_key_b, seed_name_a, seed_name_b,
        pending_count, accepted_count, rejected_count, total_count, latest_evaluated_at.
        """
        from sqlalchemy import case, literal_column

        WEC = WriteEdgeCandidate
        WS_A = WriteSeed.__table__.alias("ws_a")
        WS_B = WriteSeed.__table__.alias("ws_b")

        pending_count = func.count(case((WEC.status == "pending", 1))).label("pending_count")
        accepted_count = func.count(case((WEC.status == "accepted", 1))).label("accepted_count")
        rejected_count = func.count(case((WEC.status == "rejected", 1))).label("rejected_count")
        total_count = func.count().label("total_count")
        latest_eval = func.max(WEC.last_evaluated_at).label("latest_evaluated_at")

        base = (
            select(
                WEC.seed_key_a,
                WEC.seed_key_b,
                WS_A.c.name.label("seed_name_a"),
                WS_B.c.name.label("seed_name_b"),
                pending_count,
                accepted_count,
                rejected_count,
                total_count,
                latest_eval,
            )
            .outerjoin(WS_A, WEC.seed_key_a == WS_A.c.key)
            .outerjoin(WS_B, WEC.seed_key_b == WS_B.c.key)
            .group_by(WEC.seed_key_a, WEC.seed_key_b, WS_A.c.name, WS_B.c.name)
        )

        if min_facts > 1:
            base = base.having(func.count() >= min_facts)

        if status_filter:
            base = base.having(func.count(case((WEC.status == status_filter, 1))) > 0)

        if search:
            base = base.having(
                func.coalesce(WS_A.c.name, literal_column("''")).ilike(f"%{search}%")
                | func.coalesce(WS_B.c.name, literal_column("''")).ilike(f"%{search}%")
            )

        base = base.order_by(total_count.desc()).offset(offset).limit(limit)

        result = await self._session.execute(base)
        return [
            {
                "seed_key_a": r.seed_key_a,
                "seed_key_b": r.seed_key_b,
                "seed_name_a": r.seed_name_a,
                "seed_name_b": r.seed_name_b,
                "pending_count": r.pending_count,
                "accepted_count": r.accepted_count,
                "rejected_count": r.rejected_count,
                "total_count": r.total_count,
                "latest_evaluated_at": r.latest_evaluated_at,
            }
            for r in result.all()
        ]

    async def count_edge_candidate_pairs(
        self,
        *,
        status_filter: str | None = None,
        search: str | None = None,
        min_facts: int = 1,
    ) -> int:
        """Count distinct (seed_key_a, seed_key_b) pairs for pagination."""
        from sqlalchemy import case, literal_column

        WEC = WriteEdgeCandidate
        WS_A = WriteSeed.__table__.alias("ws_a")
        WS_B = WriteSeed.__table__.alias("ws_b")

        subq = (
            select(WEC.seed_key_a, WEC.seed_key_b)
            .outerjoin(WS_A, WEC.seed_key_a == WS_A.c.key)
            .outerjoin(WS_B, WEC.seed_key_b == WS_B.c.key)
            .group_by(WEC.seed_key_a, WEC.seed_key_b, WS_A.c.name, WS_B.c.name)
        )

        if min_facts > 1:
            subq = subq.having(func.count() >= min_facts)

        if status_filter:
            subq = subq.having(func.count(case((WEC.status == status_filter, 1))) > 0)

        if search:
            subq = subq.having(
                func.coalesce(WS_A.c.name, literal_column("''")).ilike(f"%{search}%")
                | func.coalesce(WS_B.c.name, literal_column("''")).ilike(f"%{search}%")
            )

        subq = subq.subquery()
        result = await self._session.execute(select(func.count()).select_from(subq))
        return result.scalar() or 0

    async def get_edge_candidate_pair_detail(
        self,
        seed_key_a: str,
        seed_key_b: str,
    ) -> list[WriteEdgeCandidate]:
        """Get all WriteEdgeCandidate rows for a specific pair, ordered by created_at."""
        a, b = sorted([seed_key_a, seed_key_b])
        result = await self._session.execute(
            select(WriteEdgeCandidate)
            .where(
                WriteEdgeCandidate.seed_key_a == a,
                WriteEdgeCandidate.seed_key_b == b,
            )
            .order_by(WriteEdgeCandidate.created_at)
        )
        return list(result.scalars().all())

    async def list_candidate_pairs_for_seed(
        self,
        seed_key: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict], int]:
        """List edge candidate pairs involving a specific seed. Returns (pairs, total)."""
        from sqlalchemy import case

        WEC = WriteEdgeCandidate
        WS_A = WriteSeed.__table__.alias("ws_a")
        WS_B = WriteSeed.__table__.alias("ws_b")

        pending_count = func.count(case((WEC.status == "pending", 1))).label("pending_count")
        accepted_count = func.count(case((WEC.status == "accepted", 1))).label("accepted_count")
        rejected_count = func.count(case((WEC.status == "rejected", 1))).label("rejected_count")
        total_count = func.count().label("total_count")
        latest_eval = func.max(WEC.last_evaluated_at).label("latest_evaluated_at")

        seed_filter = (WEC.seed_key_a == seed_key) | (WEC.seed_key_b == seed_key)

        base = (
            select(
                WEC.seed_key_a,
                WEC.seed_key_b,
                WS_A.c.name.label("seed_name_a"),
                WS_B.c.name.label("seed_name_b"),
                pending_count,
                accepted_count,
                rejected_count,
                total_count,
                latest_eval,
            )
            .where(seed_filter)
            .outerjoin(WS_A, WEC.seed_key_a == WS_A.c.key)
            .outerjoin(WS_B, WEC.seed_key_b == WS_B.c.key)
            .group_by(WEC.seed_key_a, WEC.seed_key_b, WS_A.c.name, WS_B.c.name)
            .order_by(total_count.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self._session.execute(base)
        pairs = [
            {
                "seed_key_a": r.seed_key_a,
                "seed_key_b": r.seed_key_b,
                "seed_name_a": r.seed_name_a,
                "seed_name_b": r.seed_name_b,
                "pending_count": r.pending_count,
                "accepted_count": r.accepted_count,
                "rejected_count": r.rejected_count,
                "total_count": r.total_count,
                "latest_evaluated_at": r.latest_evaluated_at,
            }
            for r in result.all()
        ]

        # Count
        count_subq = (
            select(WEC.seed_key_a, WEC.seed_key_b)
            .where(seed_filter)
            .group_by(WEC.seed_key_a, WEC.seed_key_b)
            .subquery()
        )
        count_result = await self._session.execute(select(func.count()).select_from(count_subq))
        total = count_result.scalar() or 0

        return pairs, total

    async def find_similar_seeds(
        self,
        name: str,
        node_type: str,
        limit: int = 10,
        threshold: float = 0.3,
    ) -> list[WriteSeed]:
        """Find seeds with similar names using trigram similarity."""
        result = await self._session.execute(
            select(WriteSeed)
            .where(
                WriteSeed.node_type == node_type,
                WriteSeed.status.in_(["active", "promoted"]),
                func.similarity(WriteSeed.name, name) >= threshold,
            )
            .order_by(func.similarity(WriteSeed.name, name).desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def search_by_trigram(
        self,
        query: str,
        *,
        node_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
        threshold: float = 0.25,
        exclude_merged: bool = True,
    ) -> list[WriteSeed]:
        """Search seeds by trigram similarity (pg_trgm) across all types.

        Returns seeds ordered by similarity score descending.
        """
        stmt = select(WriteSeed).where(func.similarity(WriteSeed.name, query) >= threshold)
        if node_type:
            stmt = stmt.where(WriteSeed.node_type == node_type)
        if status:
            stmt = stmt.where(WriteSeed.status == status)
        elif exclude_merged:
            stmt = stmt.where(WriteSeed.status.notin_(["merged", "garbage"]))
        stmt = stmt.order_by(func.similarity(WriteSeed.name, query).desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_seed_fact_ids(self, seed_key: str) -> list[uuid.UUID]:
        """Return all fact IDs linked to a seed (excluding source_attribution)."""
        result = await self._session.execute(
            select(WriteSeedFact.fact_id).where(
                WriteSeedFact.seed_key == seed_key,
                WriteSeedFact.extraction_role != "source_attribution",
            )
        )
        return [row[0] for row in result.all()]

    # ── Fact-staleness detection ─────────────────────────────────────

    async def get_fact_stale_nodes(
        self,
        threshold: int,
    ) -> list[dict]:
        """Return ALL promoted seeds whose nodes have accumulated enough new facts.

        Compares seed.fact_count against write_node.facts_at_last_build.
        Returns the full list in one shot so callers can batch dispatch
        without re-querying (which could pick up new nodes mid-loop).

        Returns dicts with seed_key, promoted_node_key, fact_count,
        facts_at_last_build, delta, enrichment_status.
        """
        from sqlalchemy import text

        stmt = text("""
            SELECT ws.key AS seed_key,
                   ws.promoted_node_key,
                   ws.fact_count,
                   wn.facts_at_last_build,
                   wn.enrichment_status,
                   (ws.fact_count - COALESCE(wn.facts_at_last_build, 0)) AS delta
            FROM write_seeds ws
            JOIN write_nodes wn ON ws.promoted_node_key = wn.key
            WHERE ws.status = 'promoted'
              AND (ws.fact_count - COALESCE(wn.facts_at_last_build, 0)) >= :threshold
            ORDER BY (ws.fact_count - COALESCE(wn.facts_at_last_build, 0)) DESC
        """)
        result = await self._session.execute(stmt, {"threshold": threshold})
        return [
            {
                "seed_key": row.seed_key,
                "promoted_node_key": row.promoted_node_key,
                "fact_count": row.fact_count,
                "facts_at_last_build": row.facts_at_last_build,
                "delta": row.delta,
                "enrichment_status": row.enrichment_status,
            }
            for row in result.all()
        ]

    # ── Auto-build queries ────────────────────────────────────────────

    async def get_promotable_seeds(
        self,
        min_facts: int,
        limit: int = 100,
    ) -> list[WriteSeed]:
        """Return active seeds with fact_count >= threshold, ordered by fact_count DESC."""
        result = await self._session.execute(
            select(WriteSeed)
            .where(
                WriteSeed.status == "active",
                WriteSeed.fact_count >= min_facts,
            )
            .order_by(WriteSeed.fact_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_buildable_edge_pairs(
        self,
        min_shared: int,
        limit: int = 500,
    ) -> list[tuple[str, str, int, list[str]]]:
        """Return seed pairs with enough shared facts for edge creation.

        Queries WriteEdgeCandidate grouped by (seed_key_a, seed_key_b) where
        both seeds are promoted, HAVING COUNT(*) >= min_shared.

        Returns list of (seed_key_a, seed_key_b, shared_count, fact_ids).
        """
        from sqlalchemy import literal_column
        from sqlalchemy.dialects.postgresql import array_agg

        stmt = (
            select(
                WriteEdgeCandidate.seed_key_a,
                WriteEdgeCandidate.seed_key_b,
                func.count().label("shared_count"),
                array_agg(WriteEdgeCandidate.fact_id).label("fact_ids"),
            )
            .where(
                WriteEdgeCandidate.status.in_(["pending", "accepted"]),
                WriteEdgeCandidate.seed_key_a.in_(select(WriteSeed.key).where(WriteSeed.status == "promoted")),
                WriteEdgeCandidate.seed_key_b.in_(select(WriteSeed.key).where(WriteSeed.status == "promoted")),
            )
            .group_by(
                WriteEdgeCandidate.seed_key_a,
                WriteEdgeCandidate.seed_key_b,
            )
            .having(func.count() >= min_shared)
            .order_by(literal_column("shared_count").desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return [(row.seed_key_a, row.seed_key_b, row.shared_count, list(row.fact_ids)) for row in result.all()]

    async def mark_seed_promoted(
        self,
        seed_key: str,
        promoted_node_key: str,
    ) -> None:
        """Mark a seed as promoted with the node key it was promoted to."""
        await self._session.execute(
            update(WriteSeed)
            .where(WriteSeed.key == seed_key)
            .values(
                status="promoted",
                promoted_node_key=promoted_node_key,
                updated_at=func.now(),
            )
        )

    async def get_merged_promoted_seeds(self, limit: int = 100) -> list[WriteSeed]:
        """Return seeds that were merged but had already been promoted to a node.

        These need their nodes absorbed into the winner's node.
        """
        result = await self._session.execute(
            select(WriteSeed)
            .where(
                WriteSeed.status == "merged",
                WriteSeed.promoted_node_key.isnot(None),
            )
            .order_by(WriteSeed.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_seed_by_promoted_node_key(self, node_key: str) -> WriteSeed | None:
        """Find a seed that was promoted to the given node key.

        Multiple seeds may share the same promoted_node_key after merging/dedup,
        so we return the first match.
        """
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.promoted_node_key == node_key).limit(1))
        return result.scalar_one_or_none()

    async def get_seeds_by_promoted_node_key(self, node_key: str) -> list[WriteSeed]:
        """Find all seeds that were promoted to the given node key."""
        result = await self._session.execute(select(WriteSeed).where(WriteSeed.promoted_node_key == node_key))
        return list(result.scalars().all())

    async def clear_promoted_node_key(self, seed_key: str) -> None:
        """Clear promoted_node_key after absorption so it won't be reprocessed."""
        await self._session.execute(
            update(WriteSeed).where(WriteSeed.key == seed_key).values(promoted_node_key=None, updated_at=func.now())
        )
