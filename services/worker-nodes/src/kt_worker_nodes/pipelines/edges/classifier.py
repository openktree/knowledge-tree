"""Edge classification via LLM.

Classifies edge candidates in batches by sending evidence facts to an LLM
that generates a justification for the relationship (no weight, no rejection).
If candidates exist, the edge exists — the LLM only explains why.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any

from kt_agents_core.prompts.edges import EDGE_RESOLUTION_SYSTEM_PROMPT
from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_db.models import Fact
from kt_worker_nodes.pipelines.edges.types import EdgeCandidate, resolve_fact_tokens

logger = logging.getLogger(__name__)


class EdgeClassifier:
    """Classifies edge candidates via LLM, generating justifications."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def classify(
        self,
        candidates: list[EdgeCandidate],
        batch_size: int | None = None,
        facts_per_type_cap: int | None = None,
        facts_per_candidate_cap: int | None = None,
    ) -> list[dict[str, Any] | None]:
        """Classify candidates in parallel batches for better LLM attention.

        Batches are built sequentially (cheap prompt construction), then all
        LLM calls fire concurrently behind a semaphore.  Results are
        reassembled in original candidate order.
        """
        settings = get_settings()
        model_id = settings.edge_resolution_model or settings.default_model
        thinking_level = settings.edge_resolution_thinking_level or None
        concurrency = settings.pipeline_concurrency

        if batch_size is None:
            batch_size = settings.edge_classification_batch_size
        if facts_per_type_cap is None:
            facts_per_type_cap = settings.edge_facts_per_type_cap
        if facts_per_candidate_cap is None:
            facts_per_candidate_cap = settings.edge_facts_per_candidate_cap

        # Build all prompts up-front (no IO, cheap)
        batches: list[tuple[int, list[EdgeCandidate], str, list[dict[int, uuid.UUID]]]] = []
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            prompt, fact_index_maps = self.build_classification_prompt(
                batch,
                facts_per_type_cap,
                facts_per_candidate_cap,
            )
            batches.append((i, batch, prompt, fact_index_maps))

        if not batches:
            return []

        # Fire all LLM calls concurrently
        sem = asyncio.Semaphore(concurrency)
        slots: list[list[dict[str, Any] | None]] = [[] for _ in batches]

        async def _classify_batch(
            idx: int, batch: list[EdgeCandidate], prompt: str, fact_index_maps: list[dict[int, uuid.UUID]]
        ) -> None:
            async with sem:
                try:
                    from kt_models.usage import clear_usage_task, set_usage_task

                    set_usage_task("edge_classification")
                    llm_result = await self._ctx.model_gateway.generate_json(
                        model_id=model_id,
                        messages=[{"role": "user", "content": prompt}],
                        system_prompt=EDGE_RESOLUTION_SYSTEM_PROMPT,
                        temperature=0.0,
                        reasoning_effort=thinking_level,
                    )
                    clear_usage_task()
                except Exception:
                    logger.exception("resolve_edges: LLM batch call failed (batch %d)", idx)
                    slots[idx] = [None for _ in batch]
                    return

                batch_decisions = self.parse_llm_decisions(llm_result, batch)

                # Resolve {fact:N} tokens -> {fact:<uuid>} in justifications
                for j, decision in enumerate(batch_decisions):
                    if decision is not None and j < len(fact_index_maps):
                        raw_justification = decision.get("justification", "")
                        if raw_justification and fact_index_maps[j]:
                            from kt_models.link_normalizer import normalize_ai_links

                            resolved = resolve_fact_tokens(
                                str(raw_justification),
                                fact_index_maps[j],
                            )
                            decision["justification"] = normalize_ai_links(resolved, preserve_fact_tokens=True)

                slots[idx] = batch_decisions

        await asyncio.gather(
            *[_classify_batch(idx, batch, prompt, fmaps) for idx, (_, batch, prompt, fmaps) in enumerate(batches)]
        )

        # Reassemble in original order
        all_decisions: list[dict[str, Any] | None] = []
        for slot in slots:
            all_decisions.extend(slot)

        return all_decisions

    @staticmethod
    def build_classification_prompt(
        candidates: list[EdgeCandidate],
        facts_per_type_cap: int = 20,
        facts_per_candidate_cap: int = 40,
    ) -> tuple[str, list[dict[int, uuid.UUID]]]:
        """Build the user message for the LLM classification call.

        Returns:
            (prompt_text, fact_index_maps) where fact_index_maps[i] is a dict
            mapping 1-based fact index -> fact UUID for the i-th candidate.
        """
        lines = ["Generate a justification for each pair:\n"]
        fact_index_maps: list[dict[int, uuid.UUID]] = []

        for i, c in enumerate(candidates, 1):
            all_facts = cap_facts_by_type(
                c.all_evidence_facts,
                facts_per_type_cap,
                facts_per_candidate_cap,
            )

            # Build index -> fact ID mapping for this candidate
            idx_map: dict[int, uuid.UUID] = {}

            lines.append(f"--- Pair {i} ---")
            lines.append(f'Concept A [{c.source_node_type}]: "{c.source_concept}"')
            lines.append(f'Concept B [{c.target_node_type}]: "{c.target_concept}"')
            lines.append(f"Shared Facts ({len(all_facts)}):")
            for j, fact in enumerate(all_facts, 1):
                idx_map[j] = fact.id
                content = fact.content[:300] if len(fact.content) > 300 else fact.content
                lines.append(f"  {j}. [{fact.fact_type}] {content}")
            lines.append("")
            fact_index_maps.append(idx_map)

        lines.append(f"Return a JSON array of {len(candidates)} objects, one per pair, in order.")
        return "\n".join(lines), fact_index_maps

    @staticmethod
    def parse_llm_decisions(
        llm_result: dict[str, Any],
        candidates: list[EdgeCandidate],
    ) -> list[dict[str, Any] | None]:
        """Parse the LLM response into a list of decisions (one per candidate).

        The LLM should return a JSON array of objects with a `justification` field.
        If it returns a dict with a list inside, we try to extract it.
        Returns None for unparseable entries.
        """
        decisions: list[dict[str, Any] | None] = [None] * len(candidates)

        raw_list: list[Any] = []
        if isinstance(llm_result, list):
            raw_list = llm_result
        elif isinstance(llm_result, dict):
            for key in ("pairs", "results", "decisions", "relationships", "classifications", "justifications"):
                if key in llm_result and isinstance(llm_result[key], list):
                    raw_list = llm_result[key]
                    break
            if not raw_list:
                if "justification" in llm_result:
                    raw_list = [llm_result]
                else:
                    for v in llm_result.values():
                        if isinstance(v, list) and v:
                            raw_list = v
                            break

        for i, item in enumerate(raw_list):
            if i >= len(candidates):
                break
            if isinstance(item, dict) and "justification" in item:
                decisions[i] = item

        return decisions


# ── Module-level helpers ─────────────────────────────────────────────


def cap_facts_by_type(
    facts: list[Fact],
    per_type_cap: int,
    total_cap: int,
) -> list[Fact]:
    """Cap facts per fact_type, then apply total cap with round-robin to preserve diversity."""
    if not facts:
        return []

    # Group by fact_type
    by_type: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        by_type[f.fact_type].append(f)

    # Cap each group
    for ft in by_type:
        by_type[ft] = by_type[ft][:per_type_cap]

    # If total is within cap, flatten and return
    all_capped: list[Fact] = []
    for group in by_type.values():
        all_capped.extend(group)
    if len(all_capped) <= total_cap:
        return all_capped

    # Round-robin across types to fill total_cap
    result: list[Fact] = []
    type_iters = {ft: iter(group) for ft, group in by_type.items()}
    active_types = list(type_iters.keys())

    while len(result) < total_cap and active_types:
        exhausted: list[str] = []
        for ft in active_types:
            if len(result) >= total_cap:
                break
            try:
                result.append(next(type_iters[ft]))
            except StopIteration:
                exhausted.append(ft)
        for ft in exhausted:
            active_types.remove(ft)

    return result
