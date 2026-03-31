"""Edge resolution: reads candidates from write-db and creates edges.

Provides EdgeResolver which reads write_edge_candidates directly and
generates justifications via LLM. Weight = fact count. No discovery
strategies — candidates are pre-populated by the seed co-occurrence system.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from typing import Any

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_db.keys import key_to_uuid, make_seed_key
from kt_db.repositories.write_seeds import WriteSeedRepository
from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
from kt_worker_nodes.pipelines.edges.types import EdgeCandidate

logger = logging.getLogger(__name__)


class EdgeResolver:
    """Resolves edges from pending write_edge_candidates for a node."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx
        self._classifier = EdgeClassifier(ctx)

    async def resolve_from_candidates(self, node: Any) -> dict[str, Any]:
        """Resolve edges from pending candidates for this node.

        Steps:
        1. Look up the seed key for the node.
        2. Query pending edge candidates from write-db.
        3. Group candidates by the other seed key.
        4. For each target seed that is promoted (has a node):
           a. Load fact objects.
           b. Call classifier for justification.
           c. Determine rel_type (same type = "related", different = "cross_type").
           d. Weight = number of fact_ids (as float, capped at 1.0).
           e. Create/update the edge.
           f. Mark candidate facts as accepted.
        5. Return aggregated metrics.
        """
        ctx = self._ctx
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is None:
            logger.debug("resolve_from_candidates: no write session available")
            return {"edges_created": 0, "edge_ids": []}

        node_type = getattr(node, "node_type", "concept")
        concept = getattr(node, "concept", "")
        seed_key = make_seed_key(node_type, concept)

        seed_repo = WriteSeedRepository(write_session)

        # Fetch pending candidates for this seed
        candidates_rows = await seed_repo.get_candidates_for_seed(seed_key, status="pending")
        if not candidates_rows:
            return {"edges_created": 0, "edge_ids": []}

        # Group by the other seed key
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in candidates_rows:
            other_key = row.seed_key_b if row.seed_key_a == seed_key else row.seed_key_a
            grouped[other_key].append(row.fact_id)

        edge_ids: list[str] = []

        for other_seed_key, fact_id_strs in grouped.items():
            try:
                # Check if target seed is promoted (has a node)
                other_seed = await seed_repo.get_seed_by_key(other_seed_key)
                if other_seed is None or not other_seed.promoted_node_key:
                    # Target seed not yet promoted — skip for now
                    logger.debug(
                        "resolve_from_candidates: target seed '%s' not promoted, skipping",
                        other_seed_key,
                    )
                    continue

                # Load fact objects
                fact_uuids = [uuid.UUID(fid) for fid in fact_id_strs if _is_valid_uuid(fid)]
                if not fact_uuids:
                    continue

                facts = await ctx.graph_engine.get_facts_by_ids(fact_uuids)
                if not facts:
                    logger.debug(
                        "resolve_from_candidates: no facts loaded for pair %s <-> %s",
                        seed_key,
                        other_seed_key,
                    )
                    continue

                # Enforce minimum shared-fact threshold
                settings = get_settings()
                if len(facts) < settings.graph_build_edge_min_shared_facts:
                    logger.debug(
                        "resolve_from_candidates: skipping pair %s <-> %s — %d facts < min %d",
                        seed_key,
                        other_seed_key,
                        len(facts),
                        settings.graph_build_edge_min_shared_facts,
                    )
                    continue

                # Determine node IDs
                source_node_id = node.id if hasattr(node, "id") else key_to_uuid(seed_key)
                target_node_id = key_to_uuid(other_seed.promoted_node_key)

                # Determine relationship type
                other_node_type = other_seed.node_type
                rel_type = "related" if node_type == other_node_type else "cross_type"

                # Build candidate object for classifier
                candidate = EdgeCandidate(
                    source_node_id=source_node_id,
                    source_concept=concept,
                    source_node_type=node_type,
                    target_node_id=target_node_id,
                    target_concept=other_seed.name,
                    target_node_type=other_node_type,
                    evidence_fact_ids=fact_uuids,
                    evidence_facts=facts,
                    source_seed_key=seed_key,
                    target_seed_key=other_seed_key,
                )

                # Get justification from classifier
                decisions = await self._classifier.classify([candidate])
                justification = ""
                if decisions and decisions[0] is not None:
                    justification = str(decisions[0].get("justification", ""))

                # Weight = log₂(fact_count + 1): clear signal without overwhelming
                weight = math.log2(len(facts) + 1)

                # Create/update the edge
                fact_id_list = [f.id for f in facts]
                edge = await ctx.graph_engine.create_edge(
                    source_node_id,
                    target_node_id,
                    rel_type,
                    weight,
                    justification=justification,
                    fact_ids=fact_id_list,
                )

                # Compute edge ID for tracking
                if edge is not None:
                    edge_id = str(edge.id)
                else:
                    from kt_db.keys import make_edge_key, make_node_key

                    sk = make_node_key(node_type, concept)
                    tk = make_node_key(other_node_type, other_seed.name)
                    ek = make_edge_key(rel_type, sk, tk)
                    edge_id = str(key_to_uuid(ek))

                edge_ids.append(edge_id)

                # Mark candidate facts as accepted
                await seed_repo.accept_candidate_facts(
                    seed_key,
                    other_seed_key,
                    fact_id_strs,
                )

                logger.info(
                    "resolve_from_candidates: created %s edge '%s' <-> '%s' (facts=%d, weight=%.2f)",
                    rel_type,
                    concept,
                    other_seed.name,
                    len(facts),
                    weight,
                )

            except Exception:
                logger.debug(
                    "resolve_from_candidates: error processing pair %s <-> %s",
                    seed_key,
                    other_seed_key,
                    exc_info=True,
                )

        if edge_ids:
            logger.info(
                "resolve_from_candidates: created %d edges for '%s'",
                len(edge_ids),
                concept,
            )

        return {"edges_created": len(edge_ids), "edge_ids": edge_ids}

    async def refresh_existing_edges(self, node: Any) -> dict[str, Any]:
        """Refresh justifications on existing edges touching this node.

        For each edge, checks if accepted candidates have added new facts
        that aren't yet in the edge's fact_ids. If the fact set changed,
        regenerates the justification via LLM and updates weight.

        Returns ``{"edges_refreshed": N}``.
        """
        from kt_db.repositories.write_edges import WriteEdgeRepository

        ctx = self._ctx
        write_session = getattr(ctx.graph_engine, "_write_session", None)
        if write_session is None:
            return {"edges_refreshed": 0}

        node_type = getattr(node, "node_type", "concept")
        concept = getattr(node, "concept", "")
        node_key = f"{node_type}:{concept}"

        edge_repo = WriteEdgeRepository(write_session)
        seed_repo = WriteSeedRepository(write_session)

        edges = await edge_repo.get_edges_for_node(node_key)
        if not edges:
            return {"edges_refreshed": 0}

        refreshed = 0

        for edge in edges:
            try:
                # Determine the other node key
                if edge.source_node_key == node_key:
                    other_key = edge.target_node_key
                else:
                    other_key = edge.source_node_key

                # Get the seed keys for both nodes
                source_seed = await seed_repo.get_seed_by_promoted_node_key(node_key)
                target_seed = await seed_repo.get_seed_by_promoted_node_key(other_key)
                if not source_seed or not target_seed:
                    continue

                # Check for accepted candidates with facts not yet in edge
                a_key, b_key = sorted([source_seed.key, target_seed.key])
                accepted_candidates = await seed_repo.get_candidates_for_seed(source_seed.key, status="accepted")
                # Filter to only candidates for this specific pair
                pair_fact_ids: list[str] = []
                for cand in accepted_candidates:
                    ca, cb = sorted([cand.seed_key_a, cand.seed_key_b])
                    if ca == a_key and cb == b_key:
                        pair_fact_ids.append(cand.fact_id)

                existing_fact_ids = set(edge.fact_ids or [])
                new_fact_ids = [fid for fid in pair_fact_ids if fid not in existing_fact_ids]

                if not new_fact_ids:
                    continue

                # Merge fact sets
                all_fact_id_strs = list(existing_fact_ids | set(new_fact_ids))
                fact_uuids = [uuid.UUID(fid) for fid in all_fact_id_strs if _is_valid_uuid(fid)]
                if not fact_uuids:
                    continue

                facts = await ctx.graph_engine.get_facts_by_ids(fact_uuids)
                if not facts:
                    continue

                # Determine other node info for candidate
                other_node_type = target_seed.node_type if edge.source_node_key == node_key else source_seed.node_type
                other_concept = target_seed.name if edge.source_node_key == node_key else source_seed.name

                source_node_id = key_to_uuid(edge.source_node_key)
                target_node_id = key_to_uuid(edge.target_node_key)

                candidate = EdgeCandidate(
                    source_node_id=source_node_id,
                    source_concept=concept if edge.source_node_key == node_key else other_concept,
                    source_node_type=node_type if edge.source_node_key == node_key else other_node_type,
                    target_node_id=target_node_id,
                    target_concept=other_concept if edge.source_node_key == node_key else concept,
                    target_node_type=other_node_type if edge.source_node_key == node_key else node_type,
                    evidence_fact_ids=fact_uuids,
                    evidence_facts=facts,
                    source_seed_key=source_seed.key if edge.source_node_key == node_key else target_seed.key,
                    target_seed_key=target_seed.key if edge.source_node_key == node_key else source_seed.key,
                )

                # Regenerate justification
                decisions = await self._classifier.classify([candidate])
                justification = ""
                if decisions and decisions[0] is not None:
                    justification = str(decisions[0].get("justification", ""))

                # Update weight and justification (raw fact count)
                weight = float(len(facts))
                await edge_repo.upsert(
                    rel_type=edge.relationship_type,
                    source_node_key=edge.source_node_key,
                    target_node_key=edge.target_node_key,
                    weight=weight,
                    justification=justification,
                    fact_ids=all_fact_id_strs,
                )

                refreshed += 1
                logger.info(
                    "refresh_existing_edges: refreshed edge '%s' <-> '%s' (+%d facts, total=%d, weight=%.2f)",
                    edge.source_node_key,
                    edge.target_node_key,
                    len(new_fact_ids),
                    len(facts),
                    weight,
                )

            except Exception:
                logger.debug(
                    "refresh_existing_edges: error processing edge %s",
                    edge.key,
                    exc_info=True,
                )

        return {"edges_refreshed": refreshed}


# ── Helpers ───────────────────────────────────────────────────────────


def _is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False
