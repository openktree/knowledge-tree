"""Admin endpoints for maintenance operations."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_api.auth.tokens import require_admin
from kt_api.dependencies import get_db_session
from kt_config.settings import get_settings
from kt_db.models import Node, User
from kt_db.repositories.roles import RoleRepository
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


# ---------------------------------------------------------------------------
# RBAC role management (admin-only)
# ---------------------------------------------------------------------------


class RoleCreate(BaseModel):
    name: str
    permissions: dict[str, bool]


class RoleUpdate(BaseModel):
    permissions: dict[str, bool]


class RoleResponse(BaseModel):
    id: str
    name: str
    permissions: dict[str, bool]
    is_system: bool

    model_config = {"from_attributes": True}


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    repo = RoleRepository(session)
    roles = await repo.list_all()
    return [{"id": str(r.id), "name": r.name, "permissions": r.permissions, "is_system": r.is_system} for r in roles]


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    repo = RoleRepository(session)
    existing = await repo.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Role '{body.name}' already exists")
    role = await repo.create(body.name, body.permissions)
    await session.commit()
    return {"id": str(role.id), "name": role.name, "permissions": role.permissions, "is_system": role.is_system}


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    body: RoleUpdate,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    repo = RoleRepository(session)
    role = await repo.update_permissions(role_id, body.permissions)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    await session.commit()
    return {"id": str(role.id), "name": role.name, "permissions": role.permissions, "is_system": role.is_system}


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repo = RoleRepository(session)
    deleted = await repo.delete(role_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Cannot delete system role or role not found")
    await session.commit()


@router.post("/users/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def assign_role_to_user(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repo = RoleRepository(session)
    role = await repo.get_by_id(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    # Verify user exists
    result = await session.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")
    await repo.assign_role(user_id, role_id)
    await session.commit()


@router.delete("/users/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role_from_user(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repo = RoleRepository(session)
    removed = await repo.remove_role(user_id, role_id)
    if not removed:
        raise HTTPException(status_code=404, detail="User-role assignment not found")
    await session.commit()


@router.get("/users/{user_id}/roles", response_model=list[RoleResponse])
async def get_user_roles(
    user_id: uuid.UUID,
    _user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    repo = RoleRepository(session)
    roles = await repo.get_user_roles(user_id)
    return [{"id": str(r.id), "name": r.name, "permissions": r.permissions, "is_system": r.is_system} for r in roles]
