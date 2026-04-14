"""Seed browsing endpoints — read-only views into the seed pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select

from kt_api.auth.tokens import require_auth
from kt_api.dependencies import get_write_session_factory_cached, require_api_key
from kt_api.schemas import (
    PaginatedPerspectiveSeedsResponse,
    PaginatedSeedsResponse,
    PerspectiveSeedPairResponse,
    PromoteSeedResponse,
    SeedDetailResponse,
    SeedFactResponse,
    SeedMergeResponse,
    SeedResponse,
    SeedRouteResponse,
    SeedTreeNode,
    SeedTreeResponse,
    SynthesizeResponse,
)
from kt_config.settings import get_settings
from kt_db.models import User
from kt_db.repositories.write_seeds import WriteSeedRepository
from kt_db.write_models import WriteFact, WriteSeed

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

router = APIRouter(prefix="/api/v1/seeds", tags=["seeds"])


def _seed_to_response(seed: WriteSeed) -> SeedResponse:
    aliases: list[str] = []
    if seed.metadata_:
        aliases = seed.metadata_.get("aliases", [])
    return SeedResponse(
        key=seed.key,
        seed_uuid=str(seed.seed_uuid),
        name=seed.name,
        node_type=seed.node_type,
        entity_subtype=seed.entity_subtype,
        status=seed.status,
        merged_into_key=seed.merged_into_key,
        promoted_node_key=seed.promoted_node_key,
        fact_count=seed.fact_count,
        source_fact_count=0,
        phonetic_code=seed.phonetic_code,
        aliases=aliases,
        created_at=seed.created_at,
        updated_at=seed.updated_at,
    )


def _seed_to_perspective_pair(seed: WriteSeed) -> PerspectiveSeedPairResponse:
    """Convert a thesis WriteSeed to a PerspectiveSeedPairResponse."""
    meta = seed.metadata_ or {}
    return PerspectiveSeedPairResponse(
        thesis_key=seed.key,
        thesis_claim=meta.get("claim", seed.name),
        antithesis_key=meta.get("antithesis_seed_key"),
        antithesis_claim=meta.get("antithesis"),
        source_concept_name=meta.get("source_concept_name"),
        scope_description=meta.get("scope_description"),
        fact_count=seed.fact_count,
        status=seed.status,
        created_at=seed.created_at,
        updated_at=seed.updated_at,
    )


async def _list_seeds_impl(
    write_session_factory: async_sessionmaker[AsyncSession],
    offset: int,
    limit: int,
    search: str | None,
    status: str | None,
    node_type: str | None,
) -> PaginatedSeedsResponse:
    """Shared implementation for listing seeds with pagination and filters."""
    settings = get_settings()
    min_fact_count: int | None = None
    effective_status = status
    if status == "promotable":
        effective_status = None
        min_fact_count = settings.seed_promotion_min_facts

    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        items = await repo.list_seeds(
            status=effective_status,
            node_type=node_type,
            search=search,
            offset=offset,
            limit=limit,
            min_fact_count=min_fact_count,
            promotable_only=status == "promotable",
        )
        total = await repo.count_seeds(
            status=effective_status,
            node_type=node_type,
            search=search,
            min_fact_count=min_fact_count,
            promotable_only=status == "promotable",
        )
        return PaginatedSeedsResponse(
            items=[_seed_to_response(s) for s in items],
            promotion_threshold=settings.seed_promotion_min_facts,
            total=total,
            offset=offset,
            limit=limit,
        )


@router.get("", response_model=PaginatedSeedsResponse)
async def list_seeds(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Filter by name (case-insensitive substring)"),
    status: str | None = Query(
        None, description="Filter by status (active, promoted, merged, ambiguous, garbage, promotable)"
    ),
    node_type: str | None = Query(None, description="Filter by node type (entity, concept, event, perspective)"),
) -> PaginatedSeedsResponse:
    """List seeds with pagination and optional filters.

    The special status ``promotable`` returns active/ambiguous seeds whose
    ``fact_count >= seed_promotion_min_facts``.
    """
    return await _list_seeds_impl(get_write_session_factory_cached(), offset, limit, search, status, node_type)


# ── Perspective seed endpoints ─────────────────────────────────────
# IMPORTANT: These must be defined BEFORE /{seed_key:path} catch-all.


@router.get("/perspectives", response_model=PaginatedPerspectiveSeedsResponse)
async def list_perspective_seeds(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Filter by claim text"),
    status: str | None = Query(None, description="Filter by status"),
    source_node_id: str | None = Query(None, description="Filter by source node ID"),
) -> PaginatedPerspectiveSeedsResponse:
    """List perspective seed pairs (thesis seeds with antithesis in metadata)."""
    write_sf = get_write_session_factory_cached()
    async with write_sf() as session:
        repo = WriteSeedRepository(session)
        seeds, total = await repo.list_perspective_pairs(
            status=status,
            search=search,
            source_node_id=source_node_id,
            offset=offset,
            limit=limit,
        )
        return PaginatedPerspectiveSeedsResponse(
            items=[_seed_to_perspective_pair(s) for s in seeds],
            total=total,
            offset=offset,
            limit=limit,
        )


@router.post(
    "/perspectives/{seed_key:path}/synthesize",
    response_model=SynthesizeResponse,
)
async def synthesize_perspective(seed_key: str) -> SynthesizeResponse:
    """Trigger synthesis of a perspective seed pair into full nodes.

    Dispatches build_composite_task for thesis + antithesis via Hatchet.
    Marks seeds as promoted.
    """
    write_sf = get_write_session_factory_cached()
    async with write_sf() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        meta = seed.metadata_ or {}
        if meta.get("dialectic_role") != "thesis":
            raise HTTPException(
                status_code=400,
                detail="Can only synthesize from thesis seeds",
            )

        if seed.status == "promoted":
            raise HTTPException(
                status_code=400,
                detail="Seed already promoted",
            )

        claim = meta.get("claim", seed.name)
        antithesis = meta.get("antithesis", "")
        antithesis_key = meta.get("antithesis_seed_key")
        source_node_ids = meta.get("source_node_ids", [])

        # Dispatch composite build via Hatchet
        from kt_hatchet.client import run_workflow

        # Build thesis
        try:
            await run_workflow(
                "build_composite",
                {
                    "node_type": "perspective",
                    "concept": claim,
                    "source_node_ids": source_node_ids,
                    "query_context": claim,
                    "parent_concept": "",
                    "metadata": {"dialectic_role": "thesis"},
                },
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to dispatch thesis synthesis: {exc}",
            ) from exc

        # Build antithesis
        if antithesis:
            try:
                await run_workflow(
                    "build_composite",
                    {
                        "node_type": "perspective",
                        "concept": antithesis,
                        "source_node_ids": source_node_ids,
                        "query_context": antithesis,
                        "parent_concept": "",
                        "metadata": {"dialectic_role": "antithesis"},
                    },
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to dispatch antithesis synthesis: {exc}",
                ) from exc

        # Mark seeds as promoted
        await repo.promote_seed(seed_key, seed_key)
        if antithesis_key:
            await repo.promote_seed(antithesis_key, antithesis_key)
        await session.commit()

        return SynthesizeResponse(
            thesis_seed_key=seed_key,
            antithesis_seed_key=antithesis_key,
            status="synthesizing",
        )


@router.delete("/perspectives/{seed_key:path}")
async def dismiss_perspective_seed(seed_key: str) -> dict[str, str]:
    """Dismiss a perspective seed pair (mark thesis + antithesis as dismissed)."""
    from sqlalchemy import update as sa_update

    write_sf = get_write_session_factory_cached()
    async with write_sf() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        meta = seed.metadata_ or {}

        # Dismiss this seed
        from kt_db.write_models import WriteSeed as WS

        await session.execute(
            sa_update(WS).where(WS.key == seed_key).values(status="dismissed", updated_at=sa_func.now())
        )

        # Also dismiss the paired seed if this is a thesis
        antithesis_key = meta.get("antithesis_seed_key")
        if antithesis_key:
            await session.execute(
                sa_update(WS).where(WS.key == antithesis_key).values(status="dismissed", updated_at=sa_func.now())
            )

        # If this is an antithesis, also dismiss the thesis
        thesis_key = meta.get("thesis_seed_key")
        if thesis_key:
            await session.execute(
                sa_update(WS).where(WS.key == thesis_key).values(status="dismissed", updated_at=sa_func.now())
            )

        await session.commit()
        return {"status": "dismissed"}


# ── Promote seed to node ──────────────────────────────────────────


@router.post(
    "/promote/{seed_key:path}",
    response_model=PromoteSeedResponse,
)
async def promote_seed_to_node(
    seed_key: str,
    user: User = Depends(require_auth),
) -> PromoteSeedResponse:
    """Promote a seed to a full node by dispatching the node pipeline.

    Only works for active seeds. Already-promoted seeds return the existing
    node ID instead of re-dispatching.
    """
    import uuid as _uuid

    write_sf = get_write_session_factory_cached()
    async with write_sf() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        if seed.status == "promoted" and seed.promoted_node_key:
            return PromoteSeedResponse(
                seed_key=seed_key,
                status="already_promoted",
                node_id=seed.promoted_node_key,
            )

        if seed.status not in ("active", "ambiguous"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot promote seed with status '{seed.status}'",
            )

    # Dispatch node pipeline via Hatchet
    from kt_api.dispatch import dispatch_with_graph

    scope_id = f"seed-promote-{_uuid.uuid4().hex[:8]}"
    require_api_key(user)  # fail-fast validation

    try:
        run_id = await dispatch_with_graph(
            "node_pipeline",
            {
                "scope_id": scope_id,
                "concept": seed.name,
                "node_type": seed.node_type,
                "entity_subtype": seed.entity_subtype,
                "seed_key": seed_key,
                "message_id": scope_id,
                "conversation_id": scope_id,
                "user_id": str(user.id),
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to dispatch node pipeline: {exc}",
        ) from exc

    return PromoteSeedResponse(
        seed_key=seed_key,
        status="started",
        workflow_run_id=run_id,
    )


# ── Mark seed as garbage ──────────────────────────────────────────


@router.post("/garbage/{seed_key:path}")
async def mark_seed_garbage(
    seed_key: str,
    reason: str | None = Query(None, description="Why this seed is garbage"),
) -> dict[str, str]:
    """Mark a seed as garbage — unpromotable, hidden by default, attracts future junk.

    Only active or ambiguous seeds can be marked as garbage.
    """
    write_sf = get_write_session_factory_cached()
    async with write_sf() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        if seed.status not in ("active", "ambiguous"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark seed as garbage with status '{seed.status}'",
            )

        success = await repo.mark_as_garbage(seed_key, reason=reason)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to mark seed as garbage")
        await session.commit()
        return {"status": "garbage", "seed_key": seed_key}


# ── Catch-all seed detail endpoints ─────────────────────────────────
# These use {seed_key:path} which matches anything — must be LAST.


async def _get_seed_tree_impl(
    write_session_factory: async_sessionmaker[AsyncSession],
    seed_key: str,
) -> SeedTreeResponse:
    """Shared implementation for getting a seed disambiguation tree."""
    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        tree = await repo.get_seed_tree(seed_key)
        if not tree:
            raise HTTPException(status_code=404, detail="Seed not found")
        return SeedTreeResponse(root=SeedTreeNode(**tree), focus_key=seed_key)


@router.get("/tree/{seed_key:path}", response_model=SeedTreeResponse)
async def get_seed_tree(seed_key: str) -> SeedTreeResponse:
    """Get the full disambiguation tree for a seed (walks up to root, then down to leaves)."""
    return await _get_seed_tree_impl(get_write_session_factory_cached(), seed_key)


async def _get_seed_impl(
    write_session_factory: async_sessionmaker[AsyncSession],
    seed_key: str,
) -> SeedDetailResponse:
    """Shared implementation for getting full seed detail."""
    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        routes_raw = await repo.get_routes_for_parent(seed.key)
        routes: list[SeedRouteResponse] = []
        if routes_raw:
            child_keys = [r.child_seed_key for r in routes_raw]
            children = await repo.get_seeds_by_keys(child_keys)
            child_map = {c.key: c for c in children}
            for r in routes_raw:
                child = child_map.get(r.child_seed_key)
                routes.append(
                    SeedRouteResponse(
                        child_key=r.child_seed_key,
                        child_name=child.name if child else r.child_seed_key,
                        child_status=child.status if child else "unknown",
                        child_fact_count=child.fact_count if child else 0,
                        label=r.label,
                    )
                )

        merges_raw = await repo.get_merges_for_seed(seed.key)
        merges = [
            SeedMergeResponse(
                operation=m.operation,
                source_seed_key=m.source_seed_key,
                target_seed_key=m.target_seed_key,
                reason=m.reason,
                fact_count_moved=len(m.fact_ids_moved) if m.fact_ids_moved else 0,
                created_at=m.created_at,
            )
            for m in merges_raw
        ]

        seed_facts_raw = await repo.get_seed_facts(seed.key)
        fact_content_map: dict[str, str] = {}
        if seed_facts_raw:
            fact_uuids = [sf.fact_id for sf in seed_facts_raw]
            result = await session.execute(
                sa_select(WriteFact.id, WriteFact.content).where(WriteFact.id.in_(fact_uuids))
            )
            for row in result.all():
                fact_content_map[str(row[0])] = row[1]
        facts = [
            SeedFactResponse(
                fact_id=str(sf.fact_id),
                fact_content=fact_content_map.get(str(sf.fact_id)),
                confidence=sf.confidence,
                extraction_context=sf.extraction_context,
                extraction_role=getattr(sf, "extraction_role", "mentioned"),
            )
            for sf in seed_facts_raw
        ]

        parent_seed_resp: SeedResponse | None = None
        parent_route = await repo.get_route_for_child(seed.key)
        if parent_route:
            parent = await repo.get_seed_by_key(parent_route.parent_seed_key)
            if parent:
                parent_seed_resp = _seed_to_response(parent)

        aliases: list[str] = []
        if seed.metadata_:
            aliases = seed.metadata_.get("aliases", [])

        settings = get_settings()
        return SeedDetailResponse(
            key=seed.key,
            seed_uuid=str(seed.seed_uuid),
            name=seed.name,
            node_type=seed.node_type,
            entity_subtype=seed.entity_subtype,
            status=seed.status,
            merged_into_key=seed.merged_into_key,
            promoted_node_key=seed.promoted_node_key,
            fact_count=len(seed_facts_raw),
            source_fact_count=0,
            phonetic_code=seed.phonetic_code,
            aliases=aliases,
            created_at=seed.created_at,
            updated_at=seed.updated_at,
            promotion_threshold=settings.seed_promotion_min_facts,
            routes=routes,
            merges=merges,
            facts=facts,
            parent_seed=parent_seed_resp,
        )


@router.get("/{seed_key:path}", response_model=SeedDetailResponse)
async def get_seed(seed_key: str) -> SeedDetailResponse:
    """Get full detail for a single seed, including routes and merge history."""
    return await _get_seed_impl(get_write_session_factory_cached(), seed_key)
