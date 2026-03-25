"""Definition synthesis pipeline.

Generates a concise 2-4 paragraph definition of a node by synthesizing
all of its dimensions (both definitive and draft). Definitive dimensions
are prioritized over drafts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_worker_nodes.pipelines.models import CreateNodeTask

logger = logging.getLogger(__name__)

_DEFINITION_SYSTEM_PROMPT = """\
You are a knowledge synthesizer. Given a concept name and multiple model \
dimensions (analyses) of that concept, produce a unified definition.

Rules:
- Write a thorough definition covering: core identity, key characteristics, \
relationships to other concepts, and significance. Use as many paragraphs as \
needed to fully capture the concept — typically 4-12 paragraphs depending on \
complexity.
- Prioritize dimensions marked [DEFINITIVE] over those marked [DRAFT].
- Where dimensions agree, state the consensus confidently.
- Where dimensions disagree, note the disagreement rather than picking sides.
- Use clear, encyclopedic language. No hedging or filler.
- Dimensions may contain fact citation links like [description](/facts/<uuid>). \
Preserve the most important of these in your definition — especially for specific \
measurements, key claims, and disputed points. Do not invent new /facts/ links; \
only carry forward links that appear in the input dimensions.
- Return ONLY the definition text, no JSON, no markdown headers."""


class DefinitionPipeline:
    """Synthesizes node definitions from dimensions."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def generate_definition(
        self,
        node_id: Any,
        node_concept: str,
    ) -> str | None:
        """Generate a definition for a node from its dimensions.

        Skips regeneration for crystallized nodes (ontology_stable).

        Args:
            node_id: UUID of the node.
            node_concept: The concept name of the node.

        Returns:
            The generated definition text, or None if no dimensions exist.
        """
        from kt_ontology.crystallization import _is_crystallized

        ctx = self._ctx

        # Preserve crystallized definitions
        node = await ctx.graph_engine.get_node(node_id)
        if node is not None and _is_crystallized(node):
            return node.definition

        dims = await ctx.graph_engine.get_dimensions(node_id)

        if not dims:
            return None

        # Build dimension summaries for the prompt
        dim_lines: list[str] = []
        for i, dim in enumerate(dims, 1):
            status = "[DEFINITIVE]" if dim.is_definitive else f"[DRAFT, {dim.fact_count} facts]"
            dim_lines.append(f"Dimension {i} ({dim.model_id}) {status}:\n{dim.content}")

        dimensions_text = "\n\n---\n\n".join(dim_lines)

        user_msg = (
            f"Concept: {node_concept}\n\n"
            f"Number of dimensions: {len(dims)}\n\n"
            f"{dimensions_text}\n\n"
            f"Synthesize a unified definition."
        )

        model_id = ctx.model_gateway.definition_model
        thinking_level = ctx.model_gateway.definition_thinking_level

        try:
            from kt_models.usage import clear_usage_task, set_usage_task

            set_usage_task("definitions")
            definition = await ctx.model_gateway.generate(
                model_id=model_id,
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=_DEFINITION_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=8000,
                reasoning_effort=thinking_level or None,
            )
            clear_usage_task()

            if definition.strip():
                await ctx.graph_engine.set_node_definition(node_id, definition.strip())
                logger.info("Generated definition for '%s' (%d chars)", node_concept, len(definition))
                return definition.strip()
        except Exception:
            logger.exception("Error generating definition for '%s'", node_concept)

        return None

    async def generate_batch(
        self,
        tasks: list[CreateNodeTask],
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        """Generate definitions for a batch of nodes.

        Uses a three-phase approach to avoid concurrent AsyncSession access:
        1. Sequential DB reads  — fetch dimensions for each node
        2. Parallel LLM calls   — generate definitions (no DB)
        3. Sequential DB writes  — persist definitions

        Returns:
            Metrics dict with definition counts and per-node detail.
        """
        settings = get_settings()
        sem = asyncio.Semaphore(concurrency or settings.pipeline_concurrency)
        ctx = self._ctx

        empty_metrics: dict[str, Any] = {"node_count": 0, "definitions_generated": 0, "nodes": []}
        def_tasks = [t for t in tasks if t.action in ("create", "refresh") and t.node is not None]
        if not def_tasks:
            return empty_metrics

        # Phase 1 — sequential DB reads: pre-fetch dimensions
        # Falls back to in-memory dim_results if DB returns nothing (e.g. commit failed)
        task_dims: dict[str, list[Any]] = {}
        for t in def_tasks:
            try:
                dims = await ctx.graph_engine.get_dimensions(t.node.id)
                if dims:
                    task_dims[t.name] = dims
            except Exception:
                logger.debug("Error fetching dimensions for '%s'", t.name, exc_info=True)
            if t.name not in task_dims and t.dim_results:
                task_dims[t.name] = t.dim_results

        llm_tasks = [t for t in def_tasks if t.name in task_dims]
        if not llm_tasks:
            return empty_metrics

        # Phase 2 — parallel LLM calls (no DB access)
        definitions: dict[str, str] = {}

        async def _gen_def_llm(t: CreateNodeTask) -> None:
            async with sem:
                try:
                    dims = task_dims[t.name]
                    dim_lines: list[str] = []
                    for i, dim in enumerate(dims, 1):
                        if isinstance(dim, dict):
                            model_id = str(dim.get("model_id", "unknown"))
                            content = str(dim.get("content", ""))
                            status = "[DRAFT]"
                        else:
                            status = "[DEFINITIVE]" if dim.is_definitive else f"[DRAFT, {dim.fact_count} facts]"
                            model_id = str(dim.model_id)
                            content = str(dim.content)
                        dim_lines.append(f"Dimension {i} ({model_id}) {status}:\n{content}")
                    dimensions_text = "\n\n---\n\n".join(dim_lines)
                    user_msg = (
                        f"Concept: {t.node.concept}\n\n"
                        f"Number of dimensions: {len(dims)}\n\n"
                        f"{dimensions_text}\n\n"
                        f"Synthesize a unified definition."
                    )
                    model_id = ctx.model_gateway.definition_model
                    thinking_level = ctx.model_gateway.definition_thinking_level
                    from kt_models.usage import clear_usage_task, set_usage_task

                    set_usage_task("definitions")
                    definition = await ctx.model_gateway.generate(
                        model_id=model_id,
                        messages=[{"role": "user", "content": user_msg}],
                        system_prompt=_DEFINITION_SYSTEM_PROMPT,
                        temperature=0.3,
                        max_tokens=8000,
                        reasoning_effort=thinking_level or None,
                    )
                    clear_usage_task()
                    if definition.strip():
                        definitions[t.name] = definition.strip()
                except Exception:
                    logger.exception("Error generating definition for '%s'", t.name)

        results = await asyncio.gather(*[_gen_def_llm(t) for t in llm_tasks], return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("Definition generation failed for '%s': %s", llm_tasks[i].name, r)

        # Phase 3 — sequential DB writes: persist definitions
        node_details: list[dict[str, Any]] = []
        for t in llm_tasks:
            definition = definitions.get(t.name)
            if definition:
                try:
                    await ctx.graph_engine.set_node_definition(t.node.id, definition)
                    logger.info("Generated definition for '%s' (%d chars)", t.node.concept, len(definition))
                    await ctx.emit(
                        "activity_log", action=f"Synthesized definition for '{t.name}'", tool="build_pipeline"
                    )
                    if len(node_details) < 10:
                        node_details.append({"name": t.name, "definition_chars": len(definition)})
                except Exception:
                    logger.exception("Error saving definition for '%s'", t.name)

        return {
            "node_count": len(def_tasks),
            "definitions_generated": len(definitions),
            "nodes": node_details,
        }
