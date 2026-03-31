"""Health check endpoint for smoke tests and monitoring."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class ComponentHealth(BaseModel):
    status: str  # "ok" or "error"
    error: str | None = None


class HealthResponse(BaseModel):
    status: str  # "healthy" or "degraded"
    components: dict[str, ComponentHealth]


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse | JSONResponse:
    """Check connectivity to all backend services.

    Returns 200 with component statuses. Returns 503 if any critical
    component (graph-db, write-db) is unreachable.
    """
    components: dict[str, ComponentHealth] = {}

    # Graph DB
    try:
        from kt_api.dependencies import get_session_factory_cached

        factory = get_session_factory_cached()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        components["graph_db"] = ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("Health check: graph-db failed: %s", exc)
        components["graph_db"] = ComponentHealth(status="error", error=str(exc))

    # Write DB
    try:
        from kt_api.dependencies import get_write_session_factory_cached

        factory = get_write_session_factory_cached()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        components["write_db"] = ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("Health check: write-db failed: %s", exc)
        components["write_db"] = ComponentHealth(status="error", error=str(exc))

    # Redis
    try:
        import redis.asyncio as aioredis

        from kt_config.settings import get_settings

        settings = get_settings()
        r = aioredis.from_url(settings.redis_url)
        await r.ping()  # type: ignore[misc]
        await r.close()  # type: ignore[misc]
        components["redis"] = ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("Health check: redis failed: %s", exc)
        components["redis"] = ComponentHealth(status="error", error=str(exc))

    # Hatchet
    try:
        from kt_hatchet.client import get_hatchet

        h = get_hatchet()
        # Just verify the client is initialized — no network call needed
        _ = h._client
        components["hatchet"] = ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("Health check: hatchet failed: %s", exc)
        components["hatchet"] = ComponentHealth(status="error", error=str(exc))

    # Critical components: graph_db and write_db
    critical_ok = all(
        components.get(c, ComponentHealth(status="error")).status == "ok" for c in ("graph_db", "write_db")
    )

    response = HealthResponse(
        status="healthy" if critical_ok else "degraded",
        components=components,
    )

    if not critical_ok:
        return JSONResponse(content=response.model_dump(), status_code=503)

    return response
