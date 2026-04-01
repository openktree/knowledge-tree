"""Admin endpoints for maintenance operations."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.dependencies import get_db_session, get_write_db_session
from kt_config.settings import get_settings
from kt_db.models import Node
from kt_models.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/reindex")
async def reindex(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Backfill node embeddings to Qdrant for all nodes."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        return {"status": "error", "message": "No OpenRouter API key configured."}

    embedding_service = EmbeddingService()

    # Get Qdrant client
    try:
        from kt_qdrant.client import get_qdrant_client

        qdrant_client = get_qdrant_client()
        from kt_qdrant.repositories.nodes import QdrantNodeRepository

        qdrant_repo = QdrantNodeRepository(qdrant_client)
    except Exception:
        return {"status": "error", "message": "Qdrant not available."}

    stmt = select(Node)
    result = await session.execute(stmt)
    nodes = list(result.scalars().all())

    if not nodes:
        return {"status": "ok", "updated": 0, "message": "No nodes to reindex."}

    updated = 0
    errors = 0
    for node in nodes:
        try:
            embedding = await embedding_service.embed_text(node.concept)
            await qdrant_repo.upsert(node.id, embedding, node_type=node.node_type, concept=node.concept)
            updated += 1
        except Exception:
            logger.exception("Failed to embed node %s (%s)", node.id, node.concept)
            errors += 1

    return {
        "status": "ok",
        "updated": updated,
        "errors": errors,
        "total_nodes": len(nodes),
    }


@router.post("/refresh-stale")
async def refresh_stale() -> dict[str, Any]:
    """Placeholder: Refresh stale nodes by re-fetching from providers."""
    return {"status": "ok", "message": "Refresh stale operation is not yet implemented."}


# ── Skip Domains ────────────────────────────────────────────────────


class SkipDomainRequest(BaseModel):
    domain: str
    reason: str


class SkipDomainResponse(BaseModel):
    domain: str
    reason: str
    created_at: str


@router.get("/skip-domains")
async def list_skip_domains(
    write_session: AsyncSession = Depends(get_write_db_session),
) -> dict[str, Any]:
    """List all domains in the fetch skip list."""
    from kt_db.repositories.write_fetch_skip_domains import WriteFetchSkipDomainRepository

    repo = WriteFetchSkipDomainRepository(write_session)
    domains = await repo.list_all()
    return {
        "domains": [
            {
                "domain": d.domain,
                "reason": d.reason,
                "created_at": d.created_at.isoformat(),
            }
            for d in domains
        ]
    }


@router.post("/skip-domains")
async def add_skip_domain(
    body: SkipDomainRequest,
    write_session: AsyncSession = Depends(get_write_db_session),
) -> SkipDomainResponse:
    """Add a domain to the fetch skip list."""
    from kt_db.repositories.write_fetch_skip_domains import WriteFetchSkipDomainRepository

    repo = WriteFetchSkipDomainRepository(write_session)
    domain = await repo.add_domain(body.domain, body.reason)
    await write_session.commit()
    return SkipDomainResponse(
        domain=domain.domain,
        reason=domain.reason,
        created_at=domain.created_at.isoformat(),
    )


@router.delete("/skip-domains/{domain}")
async def remove_skip_domain(
    domain: str,
    write_session: AsyncSession = Depends(get_write_db_session),
) -> dict[str, Any]:
    """Remove a domain from the fetch skip list."""
    from kt_db.repositories.write_fetch_skip_domains import WriteFetchSkipDomainRepository

    repo = WriteFetchSkipDomainRepository(write_session)
    removed = await repo.remove_domain(domain)
    await write_session.commit()
    if not removed:
        return {"status": "not_found", "domain": domain}
    return {"status": "ok", "removed": domain}
