"""Admin endpoints for maintenance operations."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.permissions import require_system_permission
from kt_api.dependencies import get_db_session
from kt_config.settings import get_settings
from kt_db.models import Node, User
from kt_models.embeddings import EmbeddingService
from kt_rbac import Permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/reindex")
async def reindex(
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
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
async def refresh_stale(
    _admin: User = Depends(require_system_permission(Permission.SYSTEM_ADMIN_OPS)),
) -> dict[str, Any]:
    """Placeholder: Refresh stale nodes by re-fetching from providers."""
    return {"status": "ok", "message": "Refresh stale operation is not yet implemented."}
