"""Graph-scoped seed endpoints.

Mirrors /api/v1/seeds (read endpoints) scoped to a specific graph via
/api/v1/graphs/{graph_slug}/seeds. Uses write_session_factory from GraphContext.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select as sa_select

from kt_api.graph_context import GraphContext, get_graph_context
from kt_api.schemas import (
    PaginatedSeedsResponse,
    SeedDetailResponse,
    SeedDivergenceResponse,
    SeedFactResponse,
    SeedMergeResponse,
    SeedResponse,
    SeedRouteResponse,
    SeedTreeNode,
    SeedTreeResponse,
)
from kt_api.seeds import _seed_to_response
from kt_config.settings import get_settings
from kt_db.repositories.write_seeds import WriteSeedRepository
from kt_db.write_models import WriteFact

router = APIRouter(prefix="/api/v1/graphs/{graph_slug}/seeds", tags=["graph-seeds"])


@router.get("", response_model=PaginatedSeedsResponse)
async def list_graph_seeds(
    ctx: GraphContext = Depends(get_graph_context),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Filter by name"),
    status: str | None = Query(None, description="Filter by status"),
    node_type: str | None = Query(None, description="Filter by node type"),
) -> PaginatedSeedsResponse:
    """List seeds in a specific graph."""
    settings = get_settings()
    min_fact_count: int | None = None
    effective_status = status
    if status == "promotable":
        effective_status = None
        min_fact_count = settings.seed_promotion_min_facts

    async with ctx.write_session_factory() as session:
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


@router.get("/divergence/{seed_key:path}", response_model=SeedDivergenceResponse)
async def get_graph_seed_divergence(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedDivergenceResponse:
    """Compute fact embedding divergence for a seed in a specific graph."""
    from kt_api.dependencies import get_qdrant_client_cached
    from kt_qdrant.repositories.facts import QdrantFactRepository

    async with ctx.write_session_factory() as session:
        repo = WriteSeedRepository(session)
        seed = await repo.get_seed_by_key(seed_key)
        if not seed:
            raise HTTPException(status_code=404, detail="Seed not found")

        seed_facts = await repo.get_seed_facts(seed_key)
        fact_ids = [sf.fact_id for sf in seed_facts]

        if len(fact_ids) < 2:
            return SeedDivergenceResponse(
                seed_key=seed_key,
                fact_count=len(fact_ids),
                vectors_found=len(fact_ids),
            )

        qdrant_client = get_qdrant_client_cached()
        collection_name = f"{ctx.qdrant_collection_prefix}facts" if ctx.qdrant_collection_prefix else "facts"
        fact_repo = QdrantFactRepository(qdrant_client, collection_name)
        vectors = await fact_repo.get_vectors(fact_ids)

        if len(vectors) < 2:
            return SeedDivergenceResponse(
                seed_key=seed_key,
                fact_count=len(fact_ids),
                vectors_found=len(vectors),
            )

        import numpy as np

        vecs = np.array(list(vectors.values()))
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalized = vecs / norms
        sim_matrix = normalized @ normalized.T
        n = len(vecs)

        distances = []
        for i in range(n):
            for j in range(i + 1, n):
                distances.append(1.0 - float(sim_matrix[i, j]))

        distances_arr = np.array(distances)
        mean_dist = float(distances_arr.mean())
        std_dist = float(distances_arr.std())
        cluster_estimate = 1
        if mean_dist > 0:
            cv = std_dist / mean_dist
            if cv > 0.5 and mean_dist > 0.3:
                cluster_estimate = 2
            if cv > 0.7 and mean_dist > 0.4:
                cluster_estimate = 3

        return SeedDivergenceResponse(
            seed_key=seed_key,
            fact_count=len(fact_ids),
            vectors_found=len(vectors),
            mean_pairwise_distance=round(mean_dist, 4),
            max_pairwise_distance=round(float(distances_arr.max()), 4),
            min_pairwise_distance=round(float(distances_arr.min()), 4),
            std_pairwise_distance=round(std_dist, 4),
            cluster_estimate=cluster_estimate,
        )


@router.get("/tree/{seed_key:path}", response_model=SeedTreeResponse)
async def get_graph_seed_tree(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedTreeResponse:
    """Get the full disambiguation tree for a seed in a specific graph."""
    async with ctx.write_session_factory() as session:
        repo = WriteSeedRepository(session)
        tree = await repo.get_seed_tree(seed_key)
        if not tree:
            raise HTTPException(status_code=404, detail="Seed not found")
        return SeedTreeResponse(root=SeedTreeNode(**tree), focus_key=seed_key)


@router.get("/{seed_key:path}", response_model=SeedDetailResponse)
async def get_graph_seed(
    seed_key: str,
    ctx: GraphContext = Depends(get_graph_context),
) -> SeedDetailResponse:
    """Get full detail for a single seed in a specific graph."""
    async with ctx.write_session_factory() as session:
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
