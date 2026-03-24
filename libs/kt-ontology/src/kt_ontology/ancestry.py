"""AncestryPipeline — deterministic pipeline for ontology ancestry resolution.

Steps:
1. AI Ontology — LLM proposes full ancestry chain using ontology architect prompts
2. Base Ontology — Wikidata (or other provider) returns established taxonomy chain
3. Merge — Reconcile AI + base chains, preferring base for established categories
4. System Lookup — Match merged entries against existing graph nodes
5. Materialize — Create stub nodes for gaps and wire parent_id chain immediately
6. Output — parent_id and full ancestry_chain of real node UUIDs
"""

from __future__ import annotations

import json
import logging
import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kt_config.settings import get_settings
from kt_config.types import DEFAULT_PARENTS
from kt_db.repositories.nodes import NodeRepository
from kt_graph.engine import GraphEngine
from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway
from kt_ontology.base import AncestorEntry, AncestryChain
from kt_ontology.prompts.ontology_architect import (
    ONTOLOGY_PROMPTS,
    build_ontology_architect_user_msg,
)
from kt_ontology.registry import OntologyProviderRegistry

logger = logging.getLogger(__name__)


class AncestryResult(BaseModel):
    """Output of the ancestry pipeline."""

    parent_id: uuid.UUID | None  # immediate parent for the node (None for entities)
    nodes_created: list[uuid.UUID]  # stub node IDs created during this run
    ancestry_chain: list[uuid.UUID]  # full path from node -> root (node IDs)


class _ResolvedAncestor(BaseModel):
    """Internal: an ancestor entry matched against the system graph."""

    entry: AncestorEntry
    existing_node_id: uuid.UUID | None = None
    needs_creation: bool = False


class AncestryPipeline:
    """Determines ontological ancestry for a node by merging AI, base, and system ontology."""

    def __init__(
        self,
        session: AsyncSession,
        model_gateway: ModelGateway,
        embedding_service: EmbeddingService,
        ontology_registry: OntologyProviderRegistry,
        write_session: AsyncSession | None = None,
        qdrant_client: object | None = None,
    ) -> None:
        self._session = session
        self._model_gateway = model_gateway
        self._embedding_service = embedding_service
        self._ontology_registry = ontology_registry
        self._write_session = write_session
        self._qdrant_client = qdrant_client
        self._graph_engine = GraphEngine(
            session, embedding_service, write_session=write_session, qdrant_client=qdrant_client
        )

    async def determine_ancestry(
        self,
        node_name: str,
        node_type: str,
        definition: str | None = None,
        node_id: uuid.UUID | None = None,
        dimension_snippets: list[str] | None = None,
    ) -> AncestryResult:
        """Run the full ancestry pipeline for a node.

        Creates stub nodes for any gaps in the ancestry chain and wires
        all parent_id relationships immediately. Returns the immediate
        parent_id and the list of newly-created stub node IDs.

        Falls back to the default root parent on failure.

        Args:
            node_id: The UUID of the node being classified, used to prevent
                     self-referential parent assignments.
            dimension_snippets: Optional dimension content snippets to give
                the ontology architect richer context about the node.
        """
        settings = get_settings()
        default_parent = DEFAULT_PARENTS.get(node_type)

        if node_type == "entity":
            # Entities are relational nodes without deep ontological ancestry,
            # but they still need a root parent for graph consistency.
            entity_root = DEFAULT_PARENTS.get("entity")
            return AncestryResult(
                parent_id=entity_root,
                nodes_created=[],
                ancestry_chain=[entity_root] if entity_root else [],
            )

        if not settings.enable_ontology_ancestry:
            fallback = default_parent or DEFAULT_PARENTS.get(node_type, DEFAULT_PARENTS["concept"])
            return AncestryResult(
                parent_id=fallback,
                nodes_created=[],
                ancestry_chain=[fallback],
            )

        try:
            return await self._run_pipeline(node_name, node_type, definition, node_id, dimension_snippets)
        except Exception:
            logger.warning(
                "ancestry pipeline failed for %r, falling back to default parent",
                node_name,
                exc_info=True,
            )
            fallback = default_parent or DEFAULT_PARENTS.get(node_type, DEFAULT_PARENTS["concept"])
            return AncestryResult(
                parent_id=fallback,
                nodes_created=[],
                ancestry_chain=[fallback],
            )

    async def _run_pipeline(
        self,
        node_name: str,
        node_type: str,
        definition: str | None,
        node_id: uuid.UUID | None = None,
        dimension_snippets: list[str] | None = None,
    ) -> AncestryResult:
        """Core pipeline logic.

        After merging AI + base chains and resolving against the system graph,
        creates lightweight stub nodes for any gaps and wires every parent_id
        in the chain immediately.  The caller gets back an ``AncestryResult``
        whose ``parent_id`` is always a real, committed node UUID.
        """
        default_parent = DEFAULT_PARENTS.get(node_type, DEFAULT_PARENTS["concept"])

        logger.info(
            "ancestry: starting pipeline for %r (type=%s, node_id=%s)",
            node_name,
            node_type,
            node_id,
        )

        # Step 1: Get existing ancestors in graph for context
        existing_ancestors = await self._get_existing_ancestor_names(node_type)

        # Step 2: AI ontology proposal
        ai_chain = await self._get_ai_ancestry(node_name, node_type, definition, existing_ancestors, dimension_snippets)
        if ai_chain:
            ai_names = [a.name for a in ai_chain.ancestors]
            logger.info("ancestry: AI chain for %r: %s", node_name, " → ".join(ai_names))
        else:
            logger.warning("ancestry: AI chain returned None for %r", node_name)

        # Step 3: Base ontology (Wikidata) — try the node itself first,
        # then walk up AI-proposed ancestors looking for a Wikidata hit
        base_chain = await self._get_base_ancestry(node_name, node_type)
        if base_chain:
            base_names = [a.name for a in base_chain.ancestors]
            logger.info("ancestry: base chain for %r: %s", node_name, " → ".join(base_names))
        elif ai_chain:
            # Try higher-level AI ancestors — Wikidata may know them even if
            # the specific node isn't there (e.g. "chlorophyll" misses but
            # "biological pigments" hits)
            for ancestor in ai_chain.ancestors:
                base_chain = await self._get_base_ancestry(ancestor.name, node_type)
                if base_chain:
                    logger.info(
                        "ancestry: base chain found via AI ancestor %r: %s",
                        ancestor.name,
                        " → ".join(a.name for a in base_chain.ancestors),
                    )
                    break
        if not base_chain:
            logger.info("ancestry: no base chain found for %r", node_name)

        # Step 4: Merge chains
        merged = self._merge_chains(ai_chain, base_chain, node_name)

        if not merged:
            logger.warning("ancestry: merged chain empty for %r, using default parent", node_name)
            return AncestryResult(
                parent_id=default_parent,
                nodes_created=[],
                ancestry_chain=[default_parent],
            )

        merged_names = [e.name for e in merged]
        logger.info("ancestry: merged chain for %r: %s", node_name, " → ".join(merged_names))

        # Step 5: Resolve against system graph (exclude self-matches)
        resolved = await self._resolve_against_system(merged, node_type, exclude_node_id=node_id)

        existing = [r for r in resolved if r.existing_node_id is not None]
        new = [r for r in resolved if r.needs_creation]
        logger.info(
            "ancestry: resolved for %r — %d existing nodes, %d to create. Existing: [%s], New: [%s]",
            node_name,
            len(existing),
            len(new),
            ", ".join(f"{r.entry.name} ({r.existing_node_id})" for r in existing),
            ", ".join(r.entry.name for r in new),
        )

        # Step 6: Materialize stub nodes and wire parents (exclude self)
        result = await self._materialize_and_wire(resolved, node_type, exclude_node_id=node_id)

        logger.info(
            "ancestry: result for %r — parent_id=%s, stubs_created=%d, chain_length=%d",
            node_name,
            result.parent_id,
            len(result.nodes_created),
            len(result.ancestry_chain),
        )

        return result

    # ── Step 1: Existing ancestors ───────────────────────────────

    async def _get_existing_ancestor_names(self, node_type: str, limit: int = 30) -> list[str]:
        """Get names of existing nodes that could serve as ancestors."""
        # Search for nodes that are likely category-level (have children)
        from sqlalchemy import func, select

        from kt_db.models import Node

        stmt = (
            select(Node.concept)
            .where(Node.node_type == node_type)
            .where(Node.parent_id.isnot(None))
            .order_by(func.random())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    # ── Step 2: AI ancestry ──────────────────────────────────────

    async def _get_ai_ancestry(
        self,
        node_name: str,
        node_type: str,
        definition: str | None,
        existing_ancestors: list[str],
        dimension_snippets: list[str] | None = None,
    ) -> AncestryChain | None:
        """Call LLM to propose an ancestry chain."""
        system_prompt = ONTOLOGY_PROMPTS.get(node_type)
        if system_prompt is None:
            return None

        settings = get_settings()
        model_id = settings.ontology_model or settings.default_model

        user_msg = build_ontology_architect_user_msg(
            node_name=node_name,
            node_type=node_type,
            definition=definition,
            existing_ancestors=existing_ancestors if existing_ancestors else None,
            dimension_snippets=dimension_snippets,
        )

        raw = ""
        try:
            raw = await self._model_gateway.generate(
                model_id=model_id,
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system_prompt,
                temperature=0.0,
                max_tokens=4000,
            )
            # Strip markdown fences (```json ... ```) that some models add
            text = raw.strip()
            if text.startswith("```"):
                # Remove opening fence (```json or ```)
                first_nl = text.find("\n")
                if first_nl != -1:
                    text = text[first_nl + 1 :]
                # Remove closing fence
                if text.endswith("```"):
                    text = text[:-3].rstrip()
            chain_data = json.loads(text)
        except (json.JSONDecodeError, Exception):
            logger.warning(
                "AI ancestry parse failed for %r (raw=%s)",
                node_name,
                raw[:200],
                exc_info=True,
            )
            return None

        if not isinstance(chain_data, list) or len(chain_data) < 2:
            return None

        # Convert to AncestryChain — skip the first entry (the node itself)
        ancestors = []
        for entry in chain_data[1:]:
            if isinstance(entry, dict) and "name" in entry:
                ancestors.append(
                    AncestorEntry(
                        name=entry["name"],
                        description=entry.get("description"),
                    )
                )

        if not ancestors:
            return None

        return AncestryChain(ancestors=ancestors, source="ai")

    # ── Step 3: Base ontology ────────────────────────────────────

    async def _get_base_ancestry(self, concept_name: str, node_type: str) -> AncestryChain | None:
        """Get ancestry from the default ontology provider (e.g. Wikidata)."""
        provider = self._ontology_registry.get_default()
        if provider is None:
            return None

        try:
            if not await provider.is_available():
                return None
            return await provider.get_ancestry(concept_name, node_type)
        except Exception:
            logger.warning("base ontology provider failed for %r", concept_name, exc_info=True)
            return None

    # ── Step 4: Merge chains ─────────────────────────────────────

    def _merge_chains(
        self,
        ai_chain: AncestryChain | None,
        base_chain: AncestryChain | None,
        node_name: str,
    ) -> list[AncestorEntry]:
        """Merge AI and base ancestry chains.

        Strategy:
        - If only one chain exists, use it
        - Find lowest common ancestor between chains
        - Below LCA: interleave, preferring base ontology for established categories
        - Above LCA: follow whichever chain has more granularity
        """
        if ai_chain is None and base_chain is None:
            return []

        if ai_chain is None:
            return list(base_chain.ancestors) if base_chain else []

        if base_chain is None:
            return list(ai_chain.ancestors)

        ai_ancestors = ai_chain.ancestors
        base_ancestors = base_chain.ancestors

        # Build name sets for LCA detection
        ai_names = {a.name.lower() for a in ai_ancestors}
        base_names = {b.name.lower() for b in base_ancestors}
        common_names = ai_names & base_names

        if not common_names:
            # No common ancestors — AI chain is likely more contextual
            # Append unique base entries at the top (more general end)
            merged = list(ai_ancestors)
            seen = {a.name.lower() for a in merged}
            for b in base_ancestors:
                if b.name.lower() not in seen:
                    merged.append(b)
                    seen.add(b.name.lower())
            return merged

        # Find lowest common ancestor (first match from specific end)
        lca_idx_ai = None
        for i, a in enumerate(ai_ancestors):
            if a.name.lower() in common_names:
                lca_idx_ai = i
                break

        lca_idx_base = None
        lca_name = ai_ancestors[lca_idx_ai].name.lower() if lca_idx_ai is not None else None
        if lca_name:
            for i, b in enumerate(base_ancestors):
                if b.name.lower() == lca_name:
                    lca_idx_base = i
                    break

        if lca_idx_ai is None or lca_idx_base is None:
            # Shouldn't happen given common_names is non-empty, but fallback
            return list(ai_ancestors)

        # Below LCA: prefer base (more established)
        below_lca = list(base_ancestors[:lca_idx_base])
        if not below_lca:
            below_lca = list(ai_ancestors[:lca_idx_ai])

        # LCA itself
        lca_entry = base_ancestors[lca_idx_base]

        # Above LCA: use whichever has more granularity
        above_ai = ai_ancestors[lca_idx_ai + 1 :]
        above_base = base_ancestors[lca_idx_base + 1 :]
        above_lca = above_ai if len(above_ai) >= len(above_base) else above_base

        # Deduplicate while preserving order
        merged: list[AncestorEntry] = []
        seen: set[str] = set()
        for entry in [*below_lca, lca_entry, *above_lca]:
            key = entry.name.lower()
            if key not in seen:
                merged.append(entry)
                seen.add(key)

        return merged

    # ── Step 5: Resolve against system graph ─────────────────────

    async def _resolve_against_system(
        self,
        merged: list[AncestorEntry],
        node_type: str,
        exclude_node_id: uuid.UUID | None = None,
    ) -> list[_ResolvedAncestor]:
        """For each merged entry, check if it matches an existing graph node.

        Args:
            exclude_node_id: If set, skip matches against this node ID to
                             prevent self-referential ancestry.
        """
        settings = get_settings()
        threshold = settings.ontology_similarity_threshold
        resolved: list[_ResolvedAncestor] = []

        for entry in merged:
            # Try exact trigram match first
            trigram_matches = await self._graph_engine.search_nodes_by_trigram(
                entry.name, threshold=0.6, limit=3, node_type=node_type
            )

            exact_match = None
            for node in trigram_matches:
                # Skip the node being classified to prevent self-reference
                if exclude_node_id and node.id == exclude_node_id:
                    continue
                if node.concept.lower() == entry.name.lower():
                    exact_match = node
                    break

            if exact_match:
                resolved.append(
                    _ResolvedAncestor(
                        entry=entry,
                        existing_node_id=exact_match.id,
                        needs_creation=False,
                    )
                )
                continue

            # Try semantic similarity
            try:
                embedding = await self._embedding_service.embed_text(entry.name)
                similar = await self._graph_engine.find_similar_nodes(
                    embedding, threshold=threshold, limit=1, node_type=node_type
                )
                if similar:
                    match = similar[0]
                    # Skip self-match
                    if exclude_node_id and match.id == exclude_node_id:
                        pass  # fall through to needs_creation
                    else:
                        resolved.append(
                            _ResolvedAncestor(
                                entry=entry,
                                existing_node_id=match.id,
                                needs_creation=False,
                            )
                        )
                        continue
            except Exception:
                logger.debug("embedding search failed for ancestor %r", entry.name, exc_info=True)

            # No match — needs creation
            resolved.append(
                _ResolvedAncestor(
                    entry=entry,
                    existing_node_id=None,
                    needs_creation=True,
                )
            )

        return resolved

    # ── Seed helper ────────────────────────────────────────────

    async def _ensure_seed_for_node(
        self,
        name: str,
        node_type: str,
        embedding: list[float] | None,
        write_seed_repo: object | None,
        qdrant_seed_repo: object | None,
    ) -> None:
        """Upsert a seed for an ancestry node so every node has a seed.

        Uses ``fact_count=0`` so the upsert is a no-op for seeds that
        already exist (ON CONFLICT increments by 0).  New seeds start
        with ``fact_count=0`` — they're structural placeholders until
        decomposition links real facts to them.
        """
        if write_seed_repo is None:
            return

        from kt_db.keys import make_seed_key

        seed_key = make_seed_key(node_type, name)
        try:
            from kt_db.repositories.write_seeds import WriteSeedRepository

            repo: WriteSeedRepository = write_seed_repo  # type: ignore[assignment]
            await repo.upsert_seeds_batch(
                [
                    {
                        "key": seed_key,
                        "name": name,
                        "node_type": node_type,
                        "fact_count": 0,
                    }
                ]
            )
            logger.debug(
                "Ensured seed for ancestry node '%s' (key=%s)",
                name,
                seed_key,
            )

            # Also upsert embedding to Qdrant if available
            if embedding is not None and qdrant_seed_repo is not None:
                try:
                    await qdrant_seed_repo.upsert(  # type: ignore[union-attr]
                        seed_key=seed_key,
                        embedding=embedding,
                        name=name,
                        node_type=node_type,
                    )
                except Exception:
                    logger.debug(
                        "Failed to upsert seed embedding to Qdrant for '%s'",
                        name,
                        exc_info=True,
                    )
        except Exception:
            logger.debug(
                "Failed to ensure seed for ancestry node '%s'",
                name,
                exc_info=True,
            )

    # ── Step 6: Materialize stubs & wire parents ────────────────

    async def _materialize_and_wire(
        self,
        resolved: list[_ResolvedAncestor],
        node_type: str,
        exclude_node_id: uuid.UUID | None = None,
    ) -> AncestryResult:
        """Create stub nodes for gaps and wire parent_id on every node in the chain.

        Walks the resolved chain from general -> specific (reversed), creating
        stub DB nodes where ``needs_creation`` is True. Each node's parent_id
        is set to the node above it (or the default root for the topmost).

        After this method the caller can safely set its parent_id to
        ``result.parent_id`` — that UUID is guaranteed to exist in the DB.

        Args:
            exclude_node_id: If set, filter out any resolved entry whose
                             existing_node_id matches this UUID to prevent
                             the node from appearing in its own ancestry chain.
        """
        default_parent = DEFAULT_PARENTS.get(node_type, DEFAULT_PARENTS["concept"])

        if not resolved:
            return AncestryResult(
                parent_id=default_parent,
                nodes_created=[],
                ancestry_chain=[default_parent],
            )

        # Filter out root-level entries (e.g. "all concepts") — these already exist
        root_names = {"all concepts", "all events", "all perspectives", "all entities"}
        filtered = [r for r in resolved if r.entry.name.lower() not in root_names]

        # Filter out the node being classified to prevent self-referential parents
        if exclude_node_id:
            filtered = [r for r in filtered if r.existing_node_id != exclude_node_id]

        if not filtered:
            return AncestryResult(
                parent_id=default_parent,
                nodes_created=[],
                ancestry_chain=[default_parent],
            )

        node_repo = NodeRepository(self._session)
        nodes_created: list[uuid.UUID] = []

        # Prepare seed repository for ensuring every ancestry node has a seed
        write_seed_repo = None
        qdrant_seed_repo = None
        if self._write_session is not None:
            from kt_db.repositories.write_seeds import WriteSeedRepository

            write_seed_repo = WriteSeedRepository(self._write_session)
            if self._qdrant_client is not None:
                try:
                    from kt_qdrant.repositories.seeds import QdrantSeedRepository

                    qdrant_seed_repo = QdrantSeedRepository(self._qdrant_client)
                except Exception:
                    logger.debug("Failed to create QdrantSeedRepository", exc_info=True)

        # Walk from general -> specific (reverse order) so each node's parent
        # exists before we create the child.
        # `current_parent` tracks the parent_id for the next (more specific) node.
        current_parent = default_parent

        # Build a list of (resolved_entry, node_id) from general -> specific
        wired: list[uuid.UUID] = []

        for r in reversed(filtered):
            if r.existing_node_id is not None:
                # Existing node — try to adopt it into the chain.
                # Only re-parent if it currently points at a default root.
                use_as_parent = False
                try:
                    existing = await node_repo.get_by_id(r.existing_node_id)
                    if existing and existing.parent_id in DEFAULT_PARENTS.values():
                        # Validate before re-parenting
                        ok, reason = await self._graph_engine._validate_parent_chain(r.existing_node_id, current_parent)
                        if ok:
                            await self._graph_engine.set_parent(r.existing_node_id, current_parent)
                            use_as_parent = True
                        else:
                            logger.info(
                                "Skipping re-parent of %s → %s (%s)",
                                r.existing_node_id,
                                current_parent,
                                reason,
                            )
                    else:
                        # Node has a non-default parent already; only use it if
                        # its chain reaches a root (is valid).
                        use_as_parent = True

                    # Verify the existing node's chain reaches root before
                    # adopting it as current_parent for subsequent nodes.
                    # If not, re-parent it to current_parent (which always
                    # traces to root since we build top-down from the root).
                    if use_as_parent and existing:
                        chain_ok = await self._graph_engine.chain_reaches_root(r.existing_node_id)
                        if not chain_ok:
                            logger.info(
                                "Re-parenting existing node %s to %s — chain did not reach root",
                                r.existing_node_id,
                                current_parent,
                            )
                            ok, reason = await self._graph_engine._validate_parent_chain(
                                r.existing_node_id, current_parent
                            )
                            if ok:
                                await self._graph_engine.set_parent(r.existing_node_id, current_parent)
                            else:
                                logger.info(
                                    "Cannot re-parent %s → %s (%s), skipping",
                                    r.existing_node_id,
                                    current_parent,
                                    reason,
                                )
                                use_as_parent = False
                except Exception:
                    logger.debug(
                        "Failed to process existing node %s",
                        r.existing_node_id,
                        exc_info=True,
                    )

                if use_as_parent:
                    current_parent = r.existing_node_id
                    wired.append(r.existing_node_id)

                    # Ensure existing ancestry nodes also have a seed
                    await self._ensure_seed_for_node(
                        r.entry.name,
                        node_type,
                        None,
                        write_seed_repo,
                        qdrant_seed_repo,
                    )
            else:
                # Gap node — create a lightweight stub via GraphEngine
                # (routes to write-db with deterministic UUIDs so sync works)
                try:
                    # Embed the stub so it's discoverable by similarity search
                    # (prevents duplicate stubs across ancestry runs)
                    embedding = None
                    if self._embedding_service:
                        try:
                            embedding = await self._embedding_service.embed_text(r.entry.name)
                        except Exception:
                            logger.debug("Failed to embed stub '%s'", r.entry.name, exc_info=True)

                    stub = await self._graph_engine.create_node(
                        concept=r.entry.name,
                        node_type=node_type,
                        parent_id=current_parent,
                        embedding=embedding,
                        metadata_={"stub": True, "skip_ontology": True},
                    )
                    nodes_created.append(stub.id)
                    current_parent = stub.id
                    wired.append(stub.id)
                    logger.info(
                        "Created stub node '%s' (id=%s, parent=%s)",
                        r.entry.name,
                        stub.id,
                        current_parent,
                    )

                    # Ensure a seed exists for the stub so every node
                    # has a corresponding seed entry.
                    await self._ensure_seed_for_node(
                        r.entry.name,
                        node_type,
                        embedding,
                        write_seed_repo,
                        qdrant_seed_repo,
                    )
                except Exception:
                    logger.warning(
                        "Failed to create stub node '%s'",
                        r.entry.name,
                        exc_info=True,
                    )
                    # Skip this entry — parent stays at current_parent

        # `current_parent` now points to the most specific (closest) ancestor
        # — this is the parent_id for the original node.
        parent_id = current_parent

        # Build ancestry chain specific -> general (reverse wired list) + root
        ancestry_chain = list(reversed(wired))
        if default_parent not in ancestry_chain:
            ancestry_chain.append(default_parent)

        return AncestryResult(
            parent_id=parent_id,
            nodes_created=nodes_created,
            ancestry_chain=ancestry_chain,
        )
