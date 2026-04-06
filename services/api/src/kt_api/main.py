"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kt_api.router import api_router

logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    origins = os.environ.get("CORS_ORIGINS", "")
    if origins:
        return [o.strip() for o in origins.split(",") if o.strip()]
    return ["*"]


# Suppress noisy LiteLLM debug output ("Provider List: ..." on every call)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)

try:
    import litellm

    litellm.suppress_debug_info = True
except Exception:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    from pathlib import Path

    from kt_config.settings import get_settings

    settings = get_settings()
    Path(settings.ingest_upload_dir).mkdir(parents=True, exist_ok=True)

    # Ensure Qdrant collections exist
    try:
        from kt_api.dependencies import get_qdrant_client_cached
        from kt_qdrant.repositories.facts import QdrantFactRepository
        from kt_qdrant.repositories.nodes import QdrantNodeRepository

        qdrant = get_qdrant_client_cached()
        await QdrantFactRepository(qdrant).ensure_collection()
        await QdrantNodeRepository(qdrant).ensure_collection()
    except Exception:
        logger.warning("Failed to ensure Qdrant collections at startup", exc_info=True)

    # Recover graphs stuck in "provisioning" status (crash recovery)
    try:
        await _recover_stuck_graphs()
    except Exception:
        logger.warning("Failed to run graph provisioning recovery", exc_info=True)

    yield
    # Shutdown
    from kt_api.dependencies import reset_session_factory

    reset_session_factory()


async def _recover_stuck_graphs() -> None:
    """Mark graphs stuck in 'provisioning' status as 'error' on startup.

    If the API crashed during provisioning, graphs may be left in
    'provisioning' state forever. Marking them as 'error' allows admins
    to retry via the retry_provision endpoint (which is idempotent).
    """
    from kt_api.dependencies import get_session_factory_cached
    from kt_db.models import Graph

    sf = get_session_factory_cached()
    async with sf() as session:
        from sqlalchemy import select, update

        stmt = select(Graph).where(Graph.status == "provisioning")
        result = await session.execute(stmt)
        stuck = result.scalars().all()
        if stuck:
            slugs = [g.slug for g in stuck]
            logger.warning("Found %d graph(s) stuck in provisioning: %s — marking as error", len(stuck), slugs)
            await session.execute(update(Graph).where(Graph.status == "provisioning").values(status="error"))
            await session.commit()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Knowledge Tree", version="0.1.0", lifespan=lifespan)

    origins = _get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
