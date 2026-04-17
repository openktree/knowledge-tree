"""Fact gathering: external search, decompose, store in fact pool.

Extracted from agents/tools/gather_facts.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from kt_agents_core.state import AgentContext, PipelineState
from kt_config.settings import get_settings
from kt_db.models import Fact
from kt_db.repositories.write_page_fetch_log import WritePageFetchLogRepository
from kt_db.write_models import WriteRawSource
from kt_facts.author import AuthorInfo, SourceContext, build_author_chain, extract_author
from kt_facts.pipeline import DecompositionPipeline
from kt_models.gateway import ModelGateway
from kt_providers.search_and_fetch import filter_fresh_urls, store_and_fetch

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _ensure_write_session(ctx: AgentContext) -> AsyncGenerator[Any, None]:
    """Yield a write-db session, closing it only if we created it."""
    if ctx.graph_engine._write_session is not None:
        yield ctx.graph_engine._write_session
        return
    if ctx.write_session_factory is None:
        raise RuntimeError("write_session required for gather")
    session = ctx.write_session_factory()
    try:
        yield session
    finally:
        await session.close()


@dataclasses.dataclass(frozen=True)
class _SourceSnapshot:
    """Detached snapshot of source attributes for use after session expires."""

    uri: str
    raw_content: str | None
    provider_metadata: dict[str, Any] | None
    content_type: str | None

    @classmethod
    def from_orm(cls, s: WriteRawSource) -> _SourceSnapshot | None:
        """Create snapshot while session is still active. Returns None if no URI."""
        uri = s.uri
        if not uri:
            return None
        return cls(
            uri=uri,
            raw_content=s.raw_content,
            provider_metadata=s.provider_metadata if isinstance(s.provider_metadata, dict) else None,
            content_type=getattr(s, "content_type", None),
        )


# ── Gather summary prompt ──────────────────────────────────────────

_GATHER_SUMMARY_SYSTEM = """\
You are an analytical assistant inside a knowledge-graph builder. You will \
receive a numbered list of facts that were just extracted from web search \
results. Your job is to produce a JSON object with one key:

**content_summary** — A concise paragraph (3-6 sentences) summarizing what \
the facts cover: key topics, notable entities, events, and any conflicting or \
debatable viewpoints you notice.

Respond with ONLY the JSON object. No markdown fences, no commentary."""

_GATHER_SUMMARY_USER = """\
Here are {fact_count} facts just gathered for the scope "{scope}":

{fact_list}

Produce the JSON content summary."""


_QUERY_ANGLES = [
    "latest research developments",
    "analysis overview perspectives",
    "expert debate implications",
]


def _vary_query(base: str, round_num: int) -> str:
    """Generate a query variation for subsequent search rounds."""
    if round_num <= 1:
        return base
    idx = (round_num - 2) % len(_QUERY_ANGLES)
    return f"{base} {_QUERY_ANGLES[idx]}"


class GatherFactsPipeline:
    """Gathers facts from external sources via search + decomposition."""

    def __init__(self, ctx: AgentContext, *, graph_id: str | None = None) -> None:
        self._ctx = ctx
        self._graph_id = graph_id

    async def gather(
        self,
        search_queries: list[str],
        state: PipelineState,
        *,
        enable_summary: bool = True,
        enable_extraction: bool = False,
    ) -> dict[str, object]:
        """Search external sources, decompose into facts, store in fact pool.

        Each query costs 1 explore_budget. Facts are stored but NOT linked to nodes.

        External searches are batched upfront for all affordable queries, then
        DB storage and decomposition proceed sequentially (session constraint).

        Entity extraction now runs as part of decompose() automatically.
        The ``enable_extraction`` flag controls whether extracted_nodes are
        surfaced in the result dict for the caller to use.

        Post-processing modes (controlled by params):
        - ``enable_summary=True`` (default): LLM produces a content_summary
          giving downstream code a pre-digested overview of the facts.
        - ``enable_extraction=True``: Extracted nodes from decompose() are
          included in the result dict.
        Both can be enabled simultaneously if needed.
        """
        ctx = self._ctx

        # Determine affordable queries
        affordable_queries: list[str] = []
        for query in search_queries:
            if state.explore_remaining - len(affordable_queries) <= 0:
                break
            affordable_queries.append(query)

        if not affordable_queries:
            return {
                "queries_executed": 0,
                "facts_gathered": 0,
                "explore_used": state.explore_used,
                "explore_remaining": state.explore_remaining,
            }

        # Batch all external searches upfront (parallel HTTP)
        try:
            results_by_query = await ctx.provider_registry.search_all(affordable_queries, max_results=10)
        except Exception:
            logger.exception("gather_facts: batch external search failed")
            results_by_query = {q: [] for q in affordable_queries}

        queries_executed = 0
        total_facts_gathered = 0
        source_titles_by_query: dict[str, list[str]] = {}
        all_source_urls: list[dict[str, str]] = []  # [{url, title}]
        all_gathered_facts: list[Fact] = []
        all_extracted_nodes: list[dict[str, Any]] = []
        all_source_snapshots: list[_SourceSnapshot] = []
        all_seed_keys: list[str] = []
        all_super_sources: list[dict[str, object]] = []

        # ── Phase 1: Store sources for all queries & prepare inputs ────
        from kt_hatchet.client import run_workflow
        from kt_hatchet.models import DecomposeSourcesInput, DecomposeSourcesOutput

        # Per-query tracking for post-reconciliation
        @dataclasses.dataclass
        class _QueryPlan:
            query: str
            decomposition_attempted: bool = False
            text_input: DecomposeSourcesInput | None = None
            image_sources: list[WriteRawSource] = dataclasses.field(default_factory=list)

        query_plans: list[_QueryPlan] = []

        settings = get_settings()
        target = settings.full_text_fetch_per_budget_point
        max_rounds = settings.fetch_guarantee_max_rounds

        # Use write-db for all source storage and page fetch tracking
        async with _ensure_write_session(ctx) as write_session:
            page_log = WritePageFetchLogRepository(write_session)

            for query in affordable_queries:
                # Tentatively charge budget — refunded if decomposition yields 0 facts
                state.explore_used += 1
                queries_executed += 1

                await ctx.emit("activity_log", action=f"Gathering facts: '{query}'", tool="gather_facts")

                plan = _QueryPlan(query=query)
                seen_uris: set[str] = set()
                fetched_count = 0
                all_text_sources: list[WriteRawSource] = []

                try:
                    for search_round in range(1, max_rounds + 1):
                        # Round 1: use pre-fetched batch results; rounds 2+: new varied search
                        if search_round == 1:
                            round_results = results_by_query.get(query, [])
                        else:
                            varied = _vary_query(query, search_round)
                            try:
                                round_results = await ctx.provider_registry.search_all(
                                    varied,
                                    max_results=10,
                                )
                            except Exception:
                                logger.debug(
                                    "Extra search round %d failed for '%s'",
                                    search_round,
                                    query,
                                    exc_info=True,
                                )
                                break

                        # Deduplicate against already-seen URIs
                        fresh_results = [r for r in round_results if r.uri and r.uri not in seen_uris]
                        seen_uris.update(r.uri for r in round_results if r.uri)

                        if not fresh_results:
                            break  # search exhausted

                        # Filter via PageFetchLog (skip recently processed)
                        fresh_results, _skipped = await filter_fresh_urls(
                            fresh_results,
                            page_log,
                            settings.page_stale_days,
                        )
                        if not fresh_results:
                            continue  # all stale, try next round

                        # Track source URLs and titles
                        for r in fresh_results:
                            if r.uri and not any(s["url"] == r.uri for s in all_source_urls):
                                all_source_urls.append({"url": r.uri, "title": r.title})
                        source_titles_by_query.setdefault(query, []).extend(r.title for r in fresh_results)

                        # Fetch full text — request enough to meet remaining target
                        remaining = target - fetched_count
                        fetch_limit = min(len(fresh_results), remaining + 3)  # overshoot for failures
                        raw_sources = await store_and_fetch(
                            fresh_results[:fetch_limit],
                            ctx,
                            max_fetch_urls=fetch_limit,
                            write_session=write_session,
                        )

                        # Filter out super sources (deferred to user-initiated ingestion)
                        for s in raw_sources:
                            if getattr(s, "is_super_source", False):
                                all_super_sources.append(
                                    {
                                        "raw_source_id": str(s.id),
                                        "uri": s.uri,
                                        "title": s.title,
                                        "estimated_tokens": len(s.raw_content or "") // 4,
                                        "content_type": s.content_type,
                                    }
                                )
                                continue

                            # Count successful full-text fetches toward target
                            if s.is_full_text:
                                fetched_count += 1

                            snap = _SourceSnapshot.from_orm(s)
                            if snap:
                                all_source_snapshots.append(snap)

                            # Classify as text/image for decomposition (skip snippet-only)
                            if ctx.file_data_store and ctx.file_data_store.has(s.uri):
                                plan.image_sources.append(s)
                            elif s.is_full_text:
                                all_text_sources.append(s)

                        if fetched_count >= target:
                            break  # target met

                        if search_round > 1:
                            logger.info(
                                "gather_facts: round %d for '%s' — %d/%d fetched",
                                search_round,
                                query,
                                fetched_count,
                                target,
                            )

                    # Build decomposition input from all accumulated text sources
                    if all_text_sources or plan.image_sources:
                        plan.decomposition_attempted = True
                        if all_text_sources:
                            plan.text_input = DecomposeSourcesInput(
                                raw_source_ids=[str(s.id) for s in all_text_sources],
                                concept=query,
                                query_context=state.query,
                                message_id=getattr(state, "message_id", ""),
                                conversation_id=getattr(state, "conversation_id", ""),
                            )
                except Exception:
                    logger.exception("Error storing sources for query '%s'", query)
                    await write_session.rollback()

                query_plans.append(plan)

            # Commit all sources so durable tasks can load them
            await write_session.commit()
            await _emit_budget(ctx, state)
        # Session closed here by _ensure_write_session — connection returned
        # to pool BEFORE dispatching long-running workflows.  Holding it open
        # during decompose_sources (up to 60 min) caused the underlying
        # connection to be recycled by pool_recycle, leading to InterfaceError
        # on rollback/close.

        # ── Phase 2: Dispatch all decompose workflows concurrently ────
        # No write session is held open during this phase — text
        # decomposition runs as a separate Hatchet workflow (own session),
        # and image decomposition gets a short-lived session below.
        text_coros: list[tuple[int, Any]] = []  # (plan_index, coroutine)
        image_coros: list[tuple[int, Any]] = []

        for i, plan in enumerate(query_plans):
            if plan.text_input:
                text_coros.append((i, run_workflow("decompose_sources", plan.text_input.model_dump())))
            if plan.image_sources:
                image_coros.append(
                    (
                        i,
                        _decompose_images_with_session(
                            plan.image_sources,
                            plan.query,
                            ctx,
                            state,
                        ),
                    )
                )

        # Run all text decompositions concurrently
        text_results: dict[int, DecomposeSourcesOutput] = {}
        if text_coros:
            raw_results_list = await asyncio.gather(
                *[coro for _, coro in text_coros],
                return_exceptions=True,
            )
            for (plan_idx, _), raw_result in zip(text_coros, raw_results_list):
                if isinstance(raw_result, BaseException):
                    logger.exception(
                        "Decompose workflow failed for query '%s': %s",
                        query_plans[plan_idx].query,
                        raw_result,
                    )
                else:
                    # Workflow results are wrapped as {"task_name": output_dict};
                    # unwrap by task name before validation.
                    result = raw_result
                    task_data = result.get("decompose_sources", result) if isinstance(result, dict) else result
                    text_results[plan_idx] = DecomposeSourcesOutput.model_validate(task_data)

        # Run all image decompositions concurrently
        image_results: dict[int, Any] = {}
        if image_coros:
            raw_img_results = await asyncio.gather(
                *[coro for _, coro in image_coros],
                return_exceptions=True,
            )
            for (plan_idx, _), img_result in zip(image_coros, raw_img_results):
                if isinstance(img_result, BaseException):
                    logger.exception(
                        "Image decomposition failed for query '%s': %s",
                        query_plans[plan_idx].query,
                        img_result,
                    )
                else:
                    image_results[plan_idx] = img_result

        # ── Phase 3: Reconcile results & refund budgets ───────────────
        for i, plan in enumerate(query_plans):
            facts_from_query = 0

            if i in text_results:
                decomp = text_results[i]
                facts_from_query += decomp.total_fact_count
                for fid in decomp.fact_ids:
                    all_gathered_facts.append(Fact(id=uuid.UUID(fid), content="", fact_type=""))
                if decomp.extracted_nodes:
                    all_extracted_nodes.extend(decomp.extracted_nodes)
                if decomp.seed_keys:
                    all_seed_keys.extend(decomp.seed_keys)

            if i in image_results:
                img_result = image_results[i]
                facts_from_query += len(img_result.facts)
                all_gathered_facts.extend(img_result.facts)
                if img_result.extracted_nodes:
                    all_extracted_nodes.extend(img_result.extracted_nodes)
                if img_result.seed_keys:
                    all_seed_keys.extend(img_result.seed_keys)

            total_facts_gathered += facts_from_query
            state.gathered_fact_count += facts_from_query

            # Refund budget if decomposition was attempted but produced zero
            # facts (e.g. model JSON parse failures or workflow errors).
            if plan.decomposition_attempted and facts_from_query == 0:
                state.explore_used -= 1
                queries_executed -= 1
                logger.info(
                    "gather_facts: refunded explore_budget for query %r (decomposition produced 0 facts)",
                    plan.query,
                )

        # Dispatch seed dedup as independent Hatchet tasks (non-fatal)
        if all_seed_keys:
            try:
                from kt_hatchet.client import dispatch_workflow, inject_graph_id

                unique_keys = list(dict.fromkeys(all_seed_keys))
                batch_size = 10
                batches = [unique_keys[i : i + batch_size] for i in range(0, len(unique_keys), batch_size)]
                scope_id = getattr(state, "scope_id", "") or ""
                await asyncio.gather(
                    *[
                        dispatch_workflow(
                            "seed_dedup_batch",
                            inject_graph_id({"seed_keys": b, "scope_id": scope_id}, self._graph_id),
                        )
                        for b in batches
                    ]
                )
                logger.info(
                    "gather_facts: dispatched %d seed dedup tasks (%d seeds)",
                    len(batches),
                    len(unique_keys),
                )
            except Exception:
                logger.debug("Seed dedup dispatch failed (non-fatal)", exc_info=True)

        result: dict[str, object] = {
            "queries_executed": queries_executed,
            "facts_gathered": total_facts_gathered,
            "explore_used": state.explore_used,
            "explore_remaining": state.explore_remaining,
            "source_titles_by_query": source_titles_by_query,
            "source_urls": all_source_urls,
            # Forward the just-inserted fact UUIDs so the caller can
            # dispatch the post-job dedup workflow before autograph runs.
            "inserted_fact_ids": [str(f.id) for f in all_gathered_facts if f.id is not None],
        }

        if all_super_sources:
            result["super_sources"] = all_super_sources

        # Post-processing: summary and/or extraction
        if all_gathered_facts:
            scope = getattr(state, "scope_description", None) or getattr(state, "query", "")

            if enable_summary:
                from kt_models.expense import expense_subtask

                with expense_subtask("gather_summary"):
                    summary = await _summarize_gathered_facts(
                        all_gathered_facts,
                        ctx.model_gateway,
                        scope=scope,
                    )
                if summary:
                    result.update(summary)

            if enable_extraction:
                # Entity extraction already ran in decompose() — use those results
                extracted = all_extracted_nodes or None

                # Extract source-level authors and merge as entity suggestions
                source_author_map = await _extract_all_source_authors(
                    all_source_snapshots,
                    ctx.model_gateway,
                )
                if source_author_map:
                    result["source_authors"] = {
                        uri: {"person": a.person, "organization": a.organization}
                        for uri, a in source_author_map.items()
                        if a.person or a.organization
                    }
                    author_entities = authors_to_entity_suggestions(
                        source_author_map,
                        all_gathered_facts,
                    )
                    if author_entities:
                        extracted = merge_extracted_nodes(
                            extracted or [],
                            author_entities,
                        )

                if extracted:
                    result["extracted_nodes"] = extracted

        return result


# ── Helpers ──────────────────────────────────────────────────────────


async def _decompose_images_with_session(
    image_sources: list[WriteRawSource],
    query: str,
    ctx: AgentContext,
    state: PipelineState,
) -> Any:
    """Run image decomposition with its own short-lived write session.

    Always creates a fresh session from the factory so that the caller's
    session (if any) is not held open for the duration of decomposition.
    """
    if ctx.write_session_factory is None:
        raise RuntimeError("write_session_factory required for image decomposition")
    ws = ctx.write_session_factory()
    try:
        kwargs: dict[str, Any] = dict(
            query_context=state.query,
            file_data_store=ctx.file_data_store,
            qdrant_client=ctx.qdrant_client,
            write_session=ws,
        )
        pipeline = DecompositionPipeline(ctx.model_gateway)
        return await pipeline.decompose(
            image_sources,
            query,
            ctx.session,
            ctx.embedding_service,
            **kwargs,
        )
    finally:
        await ws.close()


async def _summarize_gathered_facts(
    facts: list[Fact],
    gateway: ModelGateway,
    *,
    scope: str = "",
) -> dict[str, Any] | None:
    """Summarise gathered facts with a fast LLM call.

    Returns a dict with ``content_summary`` — or ``None`` on failure.
    """
    if not facts:
        return None

    # Build numbered fact list
    lines: list[str] = []
    for i, f in enumerate(facts, 1):
        lines.append(f"{i}. [{f.fact_type}] {f.content}")
    fact_list = "\n".join(lines)

    user_msg = _GATHER_SUMMARY_USER.format(
        fact_count=len(facts),
        scope=scope or "general",
        fact_list=fact_list,
    )

    try:
        result = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_GATHER_SUMMARY_SYSTEM,
            temperature=0.0,
            max_tokens=800,
        )
        if not result:
            return None

        summary: dict[str, Any] = {}
        if "content_summary" in result:
            summary["content_summary"] = result["content_summary"]

        return summary if summary else None

    except Exception:
        logger.warning("Failed to summarize gathered facts", exc_info=True)
        return None


async def _extract_all_source_authors(
    source_snapshots: list[_SourceSnapshot],
    gateway: ModelGateway,
) -> dict[str, AuthorInfo]:
    """Extract authors for all unique sources in parallel.

    Accepts detached _SourceSnapshot objects (not ORM models) to avoid
    MissingGreenlet errors from expired SQLAlchemy sessions.

    Returns a mapping of source URI → AuthorInfo.
    """
    if not source_snapshots:
        return {}

    # Deduplicate by URI
    seen_uris: set[str] = set()
    unique_snapshots: list[_SourceSnapshot] = []
    for s in source_snapshots:
        if s.uri not in seen_uris:
            seen_uris.add(s.uri)
            unique_snapshots.append(s)

    async def _extract_one(source: _SourceSnapshot) -> tuple[str, AuthorInfo]:
        content = source.raw_content or ""
        header = content[:500]
        provider_meta = source.provider_metadata or {}
        pdf_meta = provider_meta.get("pdf_metadata") if isinstance(provider_meta, dict) else None
        is_pdf = bool(pdf_meta) or (source.content_type or "").startswith("application/pdf")
        ctx = SourceContext(url=source.uri, header_text=header, pdf_metadata=pdf_meta)
        chain = build_author_chain(gateway, is_pdf=is_pdf)
        author = await extract_author(chain, ctx)
        return source.uri, author

    results = await asyncio.gather(
        *[_extract_one(s) for s in unique_snapshots],
        return_exceptions=True,
    )

    author_map: dict[str, AuthorInfo] = {}
    for r in results:
        if isinstance(r, BaseException):
            logger.debug("Author extraction failed: %s", r)
            continue
        uri, author = r
        author_map[uri] = author

    return author_map


def authors_to_entity_suggestions(
    source_authors: dict[str, AuthorInfo],
    facts: list[Fact],
) -> list[dict[str, Any]]:
    """Convert extracted source-level authors into entity node suggestions.

    Each unique person becomes an entity(person) suggestion.
    Each unique organization becomes an entity(organization) suggestion.
    fact_indices cover all facts from sources where that author appeared.

    Args:
        source_authors: Mapping of source URI → AuthorInfo.
        facts: All gathered facts (used for fact_indices — currently
            all facts get linked since we don't track per-source fact ranges).
    """
    person_names: set[str] = set()
    org_names: set[str] = set()

    for author in source_authors.values():
        if author.person:
            # Handle comma-separated multiple authors
            for name in author.person.split(","):
                name = name.strip()
                if name:
                    person_names.add(name)
        if author.organization:
            org_names.add(author.organization.strip())

    # All facts get linked to author entities (we don't track per-source ranges)
    all_indices = list(range(1, len(facts) + 1))

    suggestions: list[dict[str, Any]] = []
    for name in sorted(person_names):
        suggestions.append(
            {
                "name": name,
                "node_type": "entity",
                "entity_subtype": "person",
                "fact_indices": all_indices,
                "from_author_extraction": True,
            }
        )
    for name in sorted(org_names):
        suggestions.append(
            {
                "name": name,
                "node_type": "entity",
                "entity_subtype": "organization",
                "fact_indices": all_indices,
                "from_author_extraction": True,
            }
        )
    return suggestions


def merge_extracted_nodes(
    llm_nodes: list[dict[str, Any]],
    author_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge author-derived entities with LLM-extracted nodes.

    If the LLM already extracted an author as an entity, merge fact_indices
    rather than duplicating.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for n in llm_nodes:
        by_name[n["name"].lower().strip()] = n

    for a in author_nodes:
        key = a["name"].lower().strip()
        if key in by_name:
            existing = by_name[key]
            merged = set(existing.get("fact_indices", []))
            merged.update(a.get("fact_indices", []))
            existing["fact_indices"] = sorted(merged)
            # Prefer LLM's entity_subtype if set, otherwise use ours
            if not existing.get("entity_subtype"):
                existing["entity_subtype"] = a["entity_subtype"]
        else:
            by_name[key] = a

    return list(by_name.values())


async def _emit_budget(ctx: AgentContext, state: Any) -> None:
    """Emit a budget update event."""
    budget_data: dict[str, object] = {
        "nav_remaining": max(0, state.nav_budget - state.nav_used),
        "nav_total": state.nav_budget,
        "explore_remaining": state.explore_remaining,
        "explore_total": state.explore_budget,
    }
    scope = getattr(state, "scope", None)
    if scope is not None:
        budget_data["scope"] = scope
    await ctx.emit("budget_update", data=budget_data)
