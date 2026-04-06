"""Edge candidate browsing endpoints — read-only views into seed pair co-occurrence."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_write_session_factory_cached
from kt_api.schemas import (
    EdgeCandidateFactItem,
    EdgeCandidatePairDetail,
    EdgeCandidatePairSummary,
    PaginatedEdgeCandidatePairs,
)
from kt_db.repositories.write_seeds import WriteSeedRepository
from kt_db.write_models import WriteFact

router = APIRouter(prefix="/api/v1/edge-candidates", tags=["edge-candidates"])

# Type alias for the async session context manager factory
WriteSessionFactory = Callable[..., "AsyncSession"]


# ── Shared implementations ───────────────────────────────────────


async def _list_edge_candidate_pairs_impl(
    write_session_factory: WriteSessionFactory,
    offset: int,
    limit: int,
    status: str | None,
    search: str | None,
    min_facts: int,
) -> PaginatedEdgeCandidatePairs:
    """Shared implementation for listing edge candidate pairs."""
    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        items = await repo.list_edge_candidate_pairs(
            status_filter=status,
            search=search,
            min_facts=min_facts,
            offset=offset,
            limit=limit,
        )
        total = await repo.count_edge_candidate_pairs(
            status_filter=status,
            search=search,
            min_facts=min_facts,
        )
        return PaginatedEdgeCandidatePairs(
            items=[EdgeCandidatePairSummary(**item) for item in items],
            total=total,
            offset=offset,
            limit=limit,
        )


async def _list_candidates_for_seed_impl(
    write_session_factory: WriteSessionFactory,
    seed_key: str,
    offset: int,
    limit: int,
) -> PaginatedEdgeCandidatePairs:
    """Shared implementation for listing candidates for a specific seed."""
    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        items, total = await repo.list_candidate_pairs_for_seed(
            seed_key,
            offset=offset,
            limit=limit,
        )
        return PaginatedEdgeCandidatePairs(
            items=[EdgeCandidatePairSummary(**item) for item in items],
            total=total,
            offset=offset,
            limit=limit,
        )


async def _get_edge_candidate_pair_impl(
    write_session_factory: WriteSessionFactory,
    seed_key_a: str,
    seed_key_b: str,
) -> EdgeCandidatePairDetail:
    """Shared implementation for getting edge candidate pair detail."""
    # Canonical sort
    a, b = sorted([seed_key_a, seed_key_b])

    async with write_session_factory() as session:
        repo = WriteSeedRepository(session)
        candidates = await repo.get_edge_candidate_pair_detail(a, b)
        if not candidates:
            raise HTTPException(status_code=404, detail="No candidates found for this pair")

        # Get seed names
        seeds = await repo.get_seeds_by_keys([a, b])
        seed_map = {s.key: s for s in seeds}

        # Fetch fact content
        fact_ids = list({c.fact_id for c in candidates})
        fact_uuids = []
        for fid in fact_ids:
            try:
                fact_uuids.append(uuid.UUID(fid))
            except ValueError:
                pass
        fact_content_map: dict[str, str] = {}
        if fact_uuids:
            result = await session.execute(select(WriteFact.id, WriteFact.content).where(WriteFact.id.in_(fact_uuids)))
            for row in result.all():
                fact_content_map[str(row[0])] = row[1]

        pending = sum(1 for c in candidates if c.status == "pending")
        accepted = sum(1 for c in candidates if c.status == "accepted")
        rejected = sum(1 for c in candidates if c.status == "rejected")

        return EdgeCandidatePairDetail(
            seed_key_a=a,
            seed_key_b=b,
            seed_name_a=seed_map[a].name if a in seed_map else None,
            seed_name_b=seed_map[b].name if b in seed_map else None,
            facts=[
                EdgeCandidateFactItem(
                    id=str(c.id),
                    fact_id=c.fact_id,
                    fact_content=fact_content_map.get(c.fact_id),
                    status=c.status,
                    discovery_strategy=getattr(c, "discovery_strategy", None),
                    evaluation_result=c.evaluation_result,
                    last_evaluated_at=c.last_evaluated_at,
                    created_at=c.created_at,
                )
                for c in candidates
            ],
            pending_count=pending,
            accepted_count=accepted,
            rejected_count=rejected,
        )


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("", response_model=PaginatedEdgeCandidatePairs)
async def list_edge_candidate_pairs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Filter pairs that have facts with this status"),
    search: str | None = Query(None, description="Filter by seed name (case-insensitive)"),
    min_facts: int = Query(1, ge=1, description="Minimum total facts per pair"),
) -> PaginatedEdgeCandidatePairs:
    """List edge candidate pairs with pagination and filters."""
    return await _list_edge_candidate_pairs_impl(
        get_write_session_factory_cached(), offset, limit, status, search, min_facts
    )


@router.get("/by-seed/{seed_key:path}", response_model=PaginatedEdgeCandidatePairs)
async def list_candidates_for_seed(
    seed_key: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedEdgeCandidatePairs:
    """List edge candidate pairs involving a specific seed."""
    return await _list_candidates_for_seed_impl(get_write_session_factory_cached(), seed_key, offset, limit)


@router.get("/pair", response_model=EdgeCandidatePairDetail)
async def get_edge_candidate_pair_query(
    seed_key_a: str = Query(..., description="First seed key"),
    seed_key_b: str = Query(..., description="Second seed key"),
) -> EdgeCandidatePairDetail:
    """Get full detail for a specific edge candidate pair (query-param variant)."""
    return await _get_edge_candidate_pair_impl(get_write_session_factory_cached(), seed_key_a, seed_key_b)


@router.get("/{seed_key_a:path}/{seed_key_b:path}", response_model=EdgeCandidatePairDetail)
async def get_edge_candidate_pair(
    seed_key_a: str,
    seed_key_b: str,
) -> EdgeCandidatePairDetail:
    """Get full detail for a specific edge candidate pair (path-param variant, legacy)."""
    return await _get_edge_candidate_pair_impl(get_write_session_factory_cached(), seed_key_a, seed_key_b)
