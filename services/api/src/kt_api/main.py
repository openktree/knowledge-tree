"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kt_api.router import api_router
from kt_config.errors import GraphReadOnlyError
from kt_flags.fastapi import install_middleware as install_flag_middleware

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


def _load_plugins() -> None:
    """Load plugins into plugin_registry and bridge search-provider contributions.

    Runs at module import so plugin DB migrations executed during lifespan
    startup see every registered plugin.
    """
    from kt_config.plugin import load_default_plugins
    from kt_providers.registry import bridge_plugin_search_providers

    load_default_plugins()
    bridge_plugin_search_providers()


_load_plugins()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    from pathlib import Path

    from kt_config.settings import get_settings

    settings = get_settings()
    Path(settings.ingest_upload_dir).mkdir(parents=True, exist_ok=True)

    _validate_email_verification_config(settings)

    # Run all DB migrations in-process (core + plugins + per-graph).
    # Guaranteed to complete before FastAPI serves any request.
    # Plugins providing entity extractors etc. must register before this.
    try:
        from kt_db.startup import run_startup_migrations

        await run_startup_migrations(settings)
    except Exception:
        logger.exception("Startup migrations failed — aborting API startup")
        raise

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

    # Assert no DatabaseConnection row holds the reserved "default" config_key.
    # Such a row would silently shadow the synthetic system-DB entry surfaced
    # by GET /api/v1/graphs/database-connections.
    try:
        await _assert_default_db_key_unreserved()
    except Exception:
        logger.warning("Failed to validate reserved default db config_key", exc_info=True)

    yield
    # Shutdown
    from kt_api.dependencies import reset_session_factory

    reset_session_factory()


def _validate_email_verification_config(settings) -> None:  # type: ignore[no-untyped-def]
    """Fail fast if verification is required but email sending is disabled.

    Locking users out with no way to receive verification emails is a silent
    data-quality disaster — refuse to start rather than let it happen.
    """
    from kt_config.errors import ConfigurationError

    if settings.email_verification_required and not (settings.email_enabled and settings.email_verification):
        raise ConfigurationError(
            "email_verification_required=true requires email_enabled=true AND "
            "email_verification=true — otherwise users cannot receive verification "
            "emails and will be locked out of login."
        )


async def _assert_default_db_key_unreserved() -> None:
    """Fail loudly at startup if a real database_connections row holds the
    reserved ``DEFAULT_DB_CONFIG_KEY``. The repository ``create`` guard
    catches new inserts; this catches anything that may have slipped in
    via raw SQL or a previous version that lacked the guard.
    """
    from sqlalchemy import select

    from kt_api.dependencies import get_session_factory_cached
    from kt_config.settings import DEFAULT_DB_CONFIG_KEY
    from kt_db.models import DatabaseConnection

    sf = get_session_factory_cached()
    async with sf() as session:
        stmt = select(DatabaseConnection.id).where(DatabaseConnection.config_key == DEFAULT_DB_CONFIG_KEY)
        result = await session.execute(stmt)
        row = result.first()
        if row is not None:
            logger.error(
                "DatabaseConnection row exists with reserved config_key=%r (id=%s); this row will be "
                "silently shadowed by the synthetic default entry. Either rename it or delete it.",
                DEFAULT_DB_CONFIG_KEY,
                row[0],
            )


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
        from sqlalchemy import update

        stmt = update(Graph).where(Graph.status == "provisioning").values(status="error").returning(Graph.slug)
        result = await session.execute(stmt)
        slugs = [row[0] for row in result.all()]
        if slugs:
            logger.warning("Found %d graph(s) stuck in provisioning: %s — marked as error", len(slugs), slugs)
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

    install_flag_middleware(app)

    @app.exception_handler(GraphReadOnlyError)
    async def _graph_read_only_handler(_request: Request, exc: GraphReadOnlyError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "graph_id": exc.graph_id,
                "reason": exc.reason,
            },
        )

    app.include_router(api_router)
    return app


app = create_app()
